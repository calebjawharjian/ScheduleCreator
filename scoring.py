from __future__ import annotations
from dataclasses import dataclass
from models import Day, Section, SectionKind

# Factor names as constants, so a typo is a NameError you see immediately
# rather than a weight that silently maps to nothing.
DAYS = "days"
TIME = "time"
PROFESSOR = "professor"

FACTOR_LABELS = {
    DAYS: "preferred days",
    TIME: "time window",
    PROFESSOR: "professor quality",
}


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


def score_schedule(schedule: list[Section], prefs: Preferences) -> ScoreBreakdown:
    factors = {
        DAYS: _score_days(schedule, prefs),
        TIME: _score_time(schedule, prefs),
        PROFESSOR: _score_professor(schedule, prefs),
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