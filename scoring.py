from __future__ import annotations
from dataclasses import dataclass
from models import Day, Section, SectionKind, hhmm

# Factor names as constants, so a typo is a NameError you see immediately
# rather than a weight that silently maps to nothing.
DAYS = "days"
TIME = "time"
PROFESSOR = "professor"
DAYS_ON_CAMPUS = "days_on_campus"
IDLE_TIME = "idle_time"
LATE_START = "late_start"

FACTOR_LABELS = {
    DAYS: "preferred days",
    TIME: "time window",
    PROFESSOR: "professor quality",
    DAYS_ON_CAMPUS: "days on campus",
    IDLE_TIME: "idle time between classes",
    LATE_START: "late start",
}

# Fixed denominator: NYU sections essentially never span more than the five
# weekdays, so this stays meaningful without depending on any one schedule.
MAX_CAMPUS_DAYS = 5

# Independent of prefs.time_start/time_end on purpose, so this factor doesn't
# just re-measure the TIME factor with different math. 8am is "as bad as it
# gets", noon is "no further credit for sleeping in".
EARLY_START_FLOOR = hhmm("08:00")
EARLY_START_CEILING = hhmm("12:00")


@dataclass
class FactorGap:
    name: str
    score: float          # how this schedule did on the factor, 0..1
    weight_share: float   # how much the user cares, normalized to 0..1
    gap: float            # 1.0 - score, the raw shortfall on this factor
    weighted_gap: float   # gap * weight_share, this factor's drag on the total


@dataclass
class IdealComparison:
    total: float                 # the schedule's overall score, 0..1
    total_gap: float             # 1.0 - total, distance from a perfect schedule
    factors: list[FactorGap]
    strengths: list[FactorGap]   # best first: smallest raw gap
    weaknesses: list[FactorGap]  # most costly first: largest weighted gap


@dataclass
class Preferences:
    preferred_days: set[Day]
    time_start: int                        # window opens, minutes since midnight
    time_end: int                          # window closes, minutes since midnight
    professor_ratings: dict[str, float]    # instructor name -> rating on 0..5
    weights: dict[str, float]              # factor name -> relative weight


@dataclass
class ScoreBreakdown:
    factors: dict[str, float]   # factor name -> score in [0, 1]
    total: float                # weighted average of the factors, in [0, 1]


def _all_meetings(schedule: list[Section]):
    return [m for section in schedule for m in section.meetings]


def _score_days(schedule: list[Section], prefs: Preferences) -> float:
    meetings = _all_meetings(schedule)
    if not meetings:
        return 1.0
    on_preferred = sum(1 for m in meetings if m.day in prefs.preferred_days)
    return on_preferred / len(meetings)


def _score_time(schedule: list[Section], prefs: Preferences) -> float:
    meetings = _all_meetings(schedule)
    if not meetings:
        return 1.0
    inside = sum(
        1 for m in meetings
        if m.start >= prefs.time_start and m.end <= prefs.time_end
    )
    return inside / len(meetings)


def _score_professor(schedule: list[Section], prefs: Preferences) -> float:
    lectures = [s for s in schedule if s.kind == SectionKind.LECTURE]
    ratings = [
        prefs.professor_ratings[s.instructor]
        for s in lectures
        if s.instructor in prefs.professor_ratings
    ]
    if not ratings:
        return 0.5   # no rating info: stay neutral, neither reward nor punish
    average = sum(ratings) / len(ratings)
    return average / 5.0   # normalize a 0..5 rating onto 0..1


def _score_days_on_campus(schedule: list[Section], prefs: Preferences) -> float:
    meetings = _all_meetings(schedule)
    if not meetings:
        return 1.0
    distinct_days = len({m.day for m in meetings})
    return max(0.0, (MAX_CAMPUS_DAYS - distinct_days) / (MAX_CAMPUS_DAYS - 1))


def _score_idle_time(schedule: list[Section], prefs: Preferences) -> float:
    meetings = _all_meetings(schedule)
    if not meetings:
        return 1.0

    by_day: dict[Day, list] = {}
    for m in meetings:
        by_day.setdefault(m.day, []).append(m)

    # Per-day ratio, then average across days that have classes, so one
    # brutal gap on one day isn't averaged away by four tight days.
    day_scores = []
    for day_meetings in by_day.values():
        day_meetings.sort(key=lambda m: m.start)
        span = day_meetings[-1].end - day_meetings[0].start
        if span <= 0:
            continue
        class_time = sum(m.end - m.start for m in day_meetings)
        day_scores.append(1.0 - (span - class_time) / span)

    if not day_scores:
        return 1.0
    return sum(day_scores) / len(day_scores)


def _score_late_start(schedule: list[Section], prefs: Preferences) -> float:
    meetings = _all_meetings(schedule)
    if not meetings:
        return 1.0
    earliest = min(m.start for m in meetings)
    span = EARLY_START_CEILING - EARLY_START_FLOOR
    return max(0.0, min(1.0, (earliest - EARLY_START_FLOOR) / span))


def score_schedule(schedule: list[Section], prefs: Preferences) -> ScoreBreakdown:
    factors = {
        DAYS: _score_days(schedule, prefs),
        TIME: _score_time(schedule, prefs),
        PROFESSOR: _score_professor(schedule, prefs),
        DAYS_ON_CAMPUS: _score_days_on_campus(schedule, prefs),
        IDLE_TIME: _score_idle_time(schedule, prefs),
        LATE_START: _score_late_start(schedule, prefs),
    }
    weight_total = sum(prefs.weights.get(name, 0.0) for name in factors)
    if weight_total == 0:
        total = 0.0
    else:
        total = sum(
            factors[name] * prefs.weights.get(name, 0.0) for name in factors
        ) / weight_total
    return ScoreBreakdown(factors=factors, total=total)


def rank_schedules(
    schedules: list[list[Section]], prefs: Preferences
) -> list[tuple[list[Section], ScoreBreakdown]]:
    scored = [(s, score_schedule(s, prefs)) for s in schedules]
    scored.sort(key=lambda pair: pair[1].total, reverse=True)
    return scored


@dataclass
class DistinctSchedule:
    schedule: list[Section]     # one concrete pick of section numbers
    breakdown: ScoreBreakdown
    variants: int               # section combinations producing this same week


def schedule_shape(schedule: list[Section]) -> tuple:
    """What a student actually experiences, ignoring which CRN delivers it.

    NYU's catalog is full of sections that differ only by identifier: 25 of
    EG-UY 1004's lecture sections meet Mon 12:30-13:50, and EXPOS-UA 1's 86
    sections occupy 14 distinct time slots. Nothing distinguishes them to a
    student choosing a week, so they multiply the result set without adding
    an option -- 8538 ranked schedules for one real course list collapse to
    462 genuinely different weeks.

    The signature must include *everything score_schedule reads*, or
    collapsing would merge schedules with different scores and silently hide
    the better one. That means meetings (days, times) and lecture instructors.
    Campus and non-lecture instructors are included too: scoring ignores them,
    but a Brooklyn lab is not interchangeable with a Manhattan one at the same
    hour, and including a field can only split groups, never wrongly merge.
    """
    return tuple(sorted(
        (section.course_code, section.kind, section.instructor or "",
         section.campus or "",
         tuple(sorted((m.day, m.start, m.end) for m in section.meetings)))
        for section in schedule
    ))


def distinct_schedules(
    ranked: list[tuple[list[Section], ScoreBreakdown]],
    limit: int | None = None,
) -> list[DistinctSchedule]:
    """Collapse ranked results to one entry per genuinely different week.

    The first schedule of each shape is kept as the representative, so within
    a group the highest-ranked concrete section pick is the one shown.
    Duplicates are counted rather than dropped, because "and 24 other section
    combinations meet at these same times" is real information: it tells the
    student their pick is not scarce.

    The whole list is scanned even when `limit` is set. Stopping early would
    be faster but would undercount the variants of the entries it did return,
    and a wrong count is worse than a slower one.
    """
    groups: dict[tuple, DistinctSchedule] = {}
    order: list[DistinctSchedule] = []
    for schedule, breakdown in ranked:
        shape = schedule_shape(schedule)
        existing = groups.get(shape)
        if existing is not None:
            existing.variants += 1
            continue
        entry = DistinctSchedule(schedule=schedule, breakdown=breakdown, variants=1)
        groups[shape] = entry
        order.append(entry)
    return order if limit is None else order[:limit]

def compare_to_ideal(breakdown: ScoreBreakdown, prefs: Preferences) -> IdealComparison:
    weight_total = sum(prefs.weights.get(name, 0.0) for name in breakdown.factors)

    gaps: list[FactorGap] = []
    for name, score in breakdown.factors.items():
        weight = prefs.weights.get(name, 0.0)
        share = weight / weight_total if weight_total else 0.0
        gap = 1.0 - score
        gaps.append(FactorGap(
            name=name,
            score=score,
            weight_share=share,
            gap=gap,
            weighted_gap=gap * share,
        ))

    # Strengths: what the schedule does best, by raw closeness to perfect.
    strengths = sorted(gaps, key=lambda g: g.gap)
    # Weaknesses: what actually costs the most, accounting for user priorities.
    weaknesses = sorted(gaps, key=lambda g: g.weighted_gap, reverse=True)

    return IdealComparison(
        total=breakdown.total,
        total_gap=1.0 - breakdown.total,
        factors=gaps,
        strengths=strengths,
        weaknesses=weaknesses,
    )