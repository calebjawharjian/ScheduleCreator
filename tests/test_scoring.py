import pytest

from models import Day, Meeting, Section, SectionKind, hhmm
from scoring import (
    Preferences, score_schedule, rank_schedules, compare_to_ideal,
    distinct_schedules, schedule_shape, diverse_top,
    DAYS, TIME, PROFESSOR, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START, FACTOR_LABELS,
    _score_days, _score_time, _score_professor,
    _score_days_on_campus, _score_idle_time, _score_late_start,
)

ALL_FACTORS = [DAYS, TIME, PROFESSOR, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START]


def m(day, start, end):
    return Meeting(day, hhmm(start), hhmm(end))


def lecture(meetings=(), instructor=None):
    return Section("L", "X 1", SectionKind.LECTURE, instructor, list(meetings))


def prefs(**overrides):
    base = dict(
        preferred_days={Day.MON, Day.WED},
        time_start=hhmm("12:00"),
        time_end=hhmm("18:00"),
        professor_ratings={},
        weights={name: 1 for name in ALL_FACTORS},
    )
    base.update(overrides)
    return Preferences(**base)


# --- the 0..1 contract every factor must honor -------------------------------

SCORERS = [_score_days, _score_time, _score_professor,
           _score_days_on_campus, _score_idle_time, _score_late_start]


@pytest.mark.parametrize("scorer", SCORERS)
def test_every_factor_stays_within_zero_and_one(scorer):
    """Normalizing to 0..1 is what makes incommensurable factors comparable.
    A factor escaping the range silently distorts every weighted total."""
    schedule = [lecture([m(Day.MON, "08:00", "09:00"), m(Day.TUE, "19:00", "22:00")]),
                lecture([m(Day.SAT, "07:00", "08:00")])]
    assert 0.0 <= scorer(schedule, prefs()) <= 1.0


@pytest.mark.parametrize("scorer", SCORERS)
def test_empty_schedule_never_divides_by_zero(scorer):
    assert 0.0 <= scorer([], prefs()) <= 1.0


def test_every_registered_factor_has_a_label():
    """FACTOR_LABELS is the single source of truth the CLI validates against,
    so a factor without a label would break --weight for that name."""
    breakdown = score_schedule([lecture([m(Day.MON, "13:00", "14:00")])], prefs())
    assert set(breakdown.factors) == set(FACTOR_LABELS)


# --- individual factors ------------------------------------------------------

def test_days_factor_is_share_of_meetings_on_preferred_days():
    schedule = [lecture([m(Day.MON, "13:00", "14:00"), m(Day.TUE, "13:00", "14:00")])]
    assert _score_days(schedule, prefs()) == 0.5


def test_time_factor_requires_the_whole_meeting_inside_the_window():
    """A meeting straddling the edge counts as outside, not partially inside."""
    inside = [lecture([m(Day.MON, "13:00", "14:00")])]
    straddling = [lecture([m(Day.MON, "11:30", "12:30")])]
    assert _score_time(inside, prefs()) == 1.0
    assert _score_time(straddling, prefs()) == 0.0


def test_professor_factor_is_neutral_without_ratings():
    """Currently the live behavior: names exist but ratings do not, so the
    factor is constant and contributes no discrimination to the ranking."""
    assert _score_professor([lecture(instructor="Ada")], prefs()) == 0.5


def test_professor_factor_normalizes_a_five_point_rating():
    p = prefs(professor_ratings={"Ada": 4.0})
    assert _score_professor([lecture(instructor="Ada")], p) == pytest.approx(0.8)


def test_professor_factor_ignores_recitation_instructors():
    """Recitations are usually staff-taught, so only lectures should count."""
    rec = Section("R", "X 1", SectionKind.RECITATION, "Grad TA", [])
    p = prefs(professor_ratings={"Grad TA": 5.0})
    assert _score_professor([rec], p) == 0.5


def test_fewer_days_on_campus_scores_higher():
    one_day = [lecture([m(Day.MON, "13:00", "14:00"), m(Day.MON, "15:00", "16:00")])]
    three_days = [lecture([m(Day.MON, "13:00", "14:00"),
                           m(Day.TUE, "13:00", "14:00"),
                           m(Day.WED, "13:00", "14:00")])]
    assert _score_days_on_campus(one_day, prefs()) == 1.0
    assert _score_days_on_campus(three_days, prefs()) < 1.0


def test_back_to_back_day_has_no_idle_time():
    schedule = [lecture([m(Day.MON, "13:00", "14:00"), m(Day.MON, "14:00", "15:00")])]
    assert _score_idle_time(schedule, prefs()) == 1.0


def test_idle_time_averages_per_day_so_one_bad_day_still_registers():
    """Aggregating one global ratio across the week would let four tight days
    dilute a single brutal one. One brutal day is what ruins a semester, so the
    ratio is computed per day and then averaged."""
    tight = [m(Day.MON, "13:00", "14:00"), m(Day.MON, "14:00", "15:00")]
    gaping = [m(Day.TUE, "09:00", "10:00"), m(Day.TUE, "17:00", "18:00")]

    only_tight = _score_idle_time([lecture(tight)], prefs())
    both = _score_idle_time([lecture(tight + gaping)], prefs())

    assert only_tight == 1.0
    # Tue is 2h of class across a 9h span -> ~0.22; averaged with Mon's 1.0.
    assert both == pytest.approx((1.0 + 2 / 9) / 2)


def test_a_day_with_one_meeting_contributes_no_idle_penalty():
    schedule = [lecture([m(Day.MON, "13:00", "14:00")])]
    assert _score_idle_time(schedule, prefs()) == 1.0


def test_later_first_class_scores_higher():
    early = [lecture([m(Day.MON, "08:00", "09:00")])]
    late = [lecture([m(Day.MON, "12:00", "13:00")])]
    assert _score_late_start(early, prefs()) == 0.0
    assert _score_late_start(late, prefs()) == 1.0


def test_late_start_clamps_outside_its_anchors():
    """Fixed anchors (08:00 -> 0, 12:00 -> 1) rather than reusing the
    preferred time window, so this factor doesn't just re-measure TIME."""
    dawn = [lecture([m(Day.MON, "06:00", "07:00")])]
    afternoon = [lecture([m(Day.MON, "16:00", "17:00")])]
    assert _score_late_start(dawn, prefs()) == 0.0
    assert _score_late_start(afternoon, prefs()) == 1.0


def test_earliest_meeting_drives_late_start_not_the_last():
    schedule = [lecture([m(Day.MON, "08:00", "09:00"), m(Day.TUE, "15:00", "16:00")])]
    assert _score_late_start(schedule, prefs()) == 0.0


# --- weighting and comparison ------------------------------------------------

def test_weights_are_relative_and_need_not_sum_to_one():
    schedule = [lecture([m(Day.MON, "13:00", "14:00")])]
    doubled = score_schedule(schedule, prefs(weights={n: 2 for n in ALL_FACTORS}))
    single = score_schedule(schedule, prefs(weights={n: 1 for n in ALL_FACTORS}))
    assert doubled.total == pytest.approx(single.total)


def test_zero_total_weight_does_not_crash():
    schedule = [lecture([m(Day.MON, "13:00", "14:00")])]
    assert score_schedule(schedule, prefs(weights={})).total == 0.0


def test_unweighted_factor_cannot_affect_the_total():
    """A factor at weight 0 is how you disable an inert one, e.g. professor
    quality while ratings are unavailable."""
    good_prof = [lecture([m(Day.MON, "13:00", "14:00")], instructor="Ada")]
    p = prefs(professor_ratings={"Ada": 5.0},
              weights={DAYS: 1, PROFESSOR: 0})
    without = prefs(professor_ratings={}, weights={DAYS: 1, PROFESSOR: 0})
    assert score_schedule(good_prof, p).total == score_schedule(good_prof, without).total


def test_ranking_orders_by_total_descending():
    good = [lecture([m(Day.MON, "13:00", "14:00")])]
    bad = [lecture([m(Day.SAT, "08:00", "09:00")])]
    ranked = rank_schedules([bad, good], prefs())
    assert ranked[0][0] is good
    assert ranked[0][1].total >= ranked[1][1].total


def test_compare_to_ideal_ranks_strengths_and_weaknesses_differently():
    """Strengths use the raw gap; weaknesses weight it by how much the user
    cares, so the biggest tradeoff reflects priorities, not just the worst score."""
    schedule = [lecture([m(Day.TUE, "08:00", "09:00")])]
    p = prefs(weights={DAYS: 10, TIME: 1, PROFESSOR: 1,
                       DAYS_ON_CAMPUS: 1, IDLE_TIME: 1, LATE_START: 1})
    comparison = compare_to_ideal(score_schedule(schedule, p), p)

    assert comparison.total_gap == pytest.approx(1.0 - comparison.total)
    assert comparison.strengths[0].gap <= comparison.strengths[-1].gap
    assert comparison.weaknesses[0].weighted_gap >= comparison.weaknesses[-1].weighted_gap
    # DAYS carries ten-fifteenths of the weight and scores 0, so it must lead.
    assert comparison.weaknesses[0].name == DAYS


# --- collapsing duplicate weeks ----------------------------------------------
#
# NYU's catalog is full of sections that differ only by CRN: 25 of EG-UY 1004's
# lecture sections meet Mon 12:30-13:50, and EXPOS-UA 1's 86 sections occupy 14
# time slots. Uncollapsed, a realistic course list produced a top ten that was
# one week printed ten times, while the best 3-day week sat at rank 1984.

def sec(section_id, course="X 1", kind=SectionKind.LECTURE, instructor=None,
        meetings=(), campus=None):
    return Section(section_id, course, kind, instructor, list(meetings),
                   campus=campus)


def collapse(schedules, p=None):
    return distinct_schedules(rank_schedules(schedules, p or prefs()))


def test_sections_differing_only_by_id_collapse_into_one_week():
    """The core case: same course, same slot, different CRN. A student cannot
    tell these apart, so they are one option, not three."""
    slot = [m(Day.MON, "13:00", "14:00")]
    weeks = collapse([[sec("A", meetings=slot)],
                      [sec("B", meetings=slot)],
                      [sec("C", meetings=slot)]])
    assert len(weeks) == 1
    assert weeks[0].variants == 3


def test_different_meeting_times_stay_separate():
    weeks = collapse([[sec("A", meetings=[m(Day.MON, "13:00", "14:00")])],
                      [sec("B", meetings=[m(Day.TUE, "13:00", "14:00")])]])
    assert len(weeks) == 2
    assert all(w.variants == 1 for w in weeks)


def test_different_lecture_instructors_never_collapse():
    """The safety property. Instructor feeds _score_professor, so merging two
    schedules that differ only by professor would hide the better-rated one
    behind whichever the enumeration happened to reach first."""
    slot = [m(Day.MON, "13:00", "14:00")]
    p = prefs(professor_ratings={"Ada": 5.0, "Grace": 1.0})
    weeks = collapse([[sec("A", instructor="Ada", meetings=slot)],
                      [sec("B", instructor="Grace", meetings=slot)]], p)
    assert len(weeks) == 2
    assert weeks[0].schedule[0].instructor == "Ada"      # better rating first


def test_different_campuses_never_collapse():
    """Scoring ignores campus, but a Brooklyn lab is not interchangeable with a
    Manhattan one at the same hour."""
    slot = [m(Day.MON, "13:00", "14:00")]
    weeks = collapse([[sec("A", meetings=slot, campus="Washington Square")],
                      [sec("B", meetings=slot, campus="Brooklyn (Tandon)")]])
    assert len(weeks) == 2


def test_collapsed_schedules_all_share_the_representative_score():
    """The invariant that makes collapsing safe at all: anything merged scores
    identically, so nothing better is ever discarded."""
    slot = [m(Day.MON, "13:00", "14:00")]
    other = [m(Day.WED, "09:00", "10:00")]
    schedules = [[sec(str(i), meetings=slot)] for i in range(4)]
    schedules += [[sec("z", meetings=other)]]
    p = prefs()

    for week in collapse(schedules, p):
        same_shape = [s for s in schedules
                      if schedule_shape(s) == schedule_shape(week.schedule)]
        assert len(same_shape) == week.variants
        for s in same_shape:
            assert score_schedule(s, p).total == week.breakdown.total


def test_the_representative_is_the_highest_ranked_of_its_group():
    slot = [m(Day.MON, "13:00", "14:00")]
    weeks = collapse([[sec("late", meetings=[m(Day.SAT, "08:00", "09:00")])],
                      [sec("A", meetings=slot)],
                      [sec("B", meetings=slot)]])
    assert weeks[0].schedule[0].section_id == "A"   # outranks the Saturday week


def test_distinct_order_stays_descending_by_score():
    weeks = collapse([[sec("bad", meetings=[m(Day.SAT, "08:00", "09:00")])],
                      [sec("good", meetings=[m(Day.MON, "13:00", "14:00")])]])
    assert [w.breakdown.total for w in weeks] == sorted(
        [w.breakdown.total for w in weeks], reverse=True)


def test_limit_truncates_but_variant_counts_stay_accurate():
    """Counting has to scan everything. Stopping at the limit would be faster
    and would undercount, and a wrong count is worse than a slow one."""
    slot = [m(Day.MON, "13:00", "14:00")]
    ranked = rank_schedules(
        [[sec("A", meetings=slot)], [sec("B", meetings=slot)],
         [sec("C", meetings=slot)],
         [sec("z", meetings=[m(Day.SAT, "08:00", "09:00")])]], prefs())
    weeks = distinct_schedules(ranked, limit=1)
    assert len(weeks) == 1
    assert weeks[0].variants == 3


def test_section_order_within_a_schedule_does_not_matter():
    """Bundles arrive in whatever order the cartesian product produced; the
    same week listed lecture-first or lab-first is still the same week."""
    lec = sec("L", meetings=[m(Day.MON, "13:00", "14:00")])
    lab = sec("B", kind=SectionKind.LAB, meetings=[m(Day.WED, "09:00", "11:00")])
    assert schedule_shape([lec, lab]) == schedule_shape([lab, lec])


def test_empty_input_collapses_to_nothing():
    assert distinct_schedules([]) == []


# --- reserving slots for genuinely different weeks ---------------------------
#
# Collapsing duplicates was not enough: for a real four-course list every one
# of the ten displayed weeks was a five-day week differing by one recitation,
# while the best four-day week sat at rank 583 and the best three-day week at
# rank 1984. Both existed; neither was reachable by scrolling.

def week(days, instructor=None, start="13:00"):
    """A schedule occupying the given days, one meeting each."""
    return [sec(f"{d.name}-{start}", meetings=[m(d, start, "14:00")],
                instructor=instructor) for d in days]


def alternatives(schedules, p=None, limit=10):
    p = p or prefs()
    return diverse_top(distinct_schedules(rank_schedules(schedules, p)), limit=limit)


def test_the_best_overall_schedule_is_always_first():
    """Diversity must not cost the top answer its place."""
    best = week([Day.MON])
    worse = week([Day.SAT])
    picks = alternatives([worse, best])
    assert picks[0].option.schedule is best
    assert picks[0].reason == "best overall"


def test_a_structurally_different_week_is_promoted_over_a_near_duplicate():
    """The whole point: a three-day week that ranks below several five-day
    weeks still gets a slot, because nothing shown offers it."""
    five_day = [week([Day.MON, Day.TUE, Day.WED, Day.THU, Day.FRI], start=s)
                for s in ("13:00", "13:15", "13:30", "13:45")]
    three_day = week([Day.MON, Day.TUE, Day.WED], start="09:00")

    picks = alternatives(five_day + [three_day], limit=3)
    promoted = [a for a in picks if a.option.schedule is three_day]
    assert promoted, "the three-day week should have earned a reserved slot"
    assert promoted[0].factor == DAYS_ON_CAMPUS
    assert promoted[0].reason == "best for days on campus"


def test_a_slot_is_not_spent_when_nothing_better_exists():
    """If every week is five days, 'fewest days' is not an alternative and
    must not cost a row -- those slots fall through to next-best by score."""
    same_shape = [week([Day.MON, Day.TUE, Day.WED, Day.THU, Day.FRI], start=s)
                  for s in ("13:00", "13:15", "13:30")]
    picks = alternatives(same_shape)
    assert not any(a.factor == DAYS_ON_CAMPUS for a in picks)
    assert [a.reason for a in picks[1:]] == ["next best"] * (len(picks) - 1)


def test_one_schedule_cannot_take_two_slots():
    """A week that leads on several factors is still one row. Without the
    greedy update it would be picked once per factor it happens to win."""
    all_rounder = week([Day.MON], start="11:00")
    picks = alternatives([week([Day.SAT], start="08:00"), all_rounder], limit=6)
    chosen = [id(a.option) for a in picks]
    assert len(chosen) == len(set(chosen))


def test_alternatives_never_repeat_a_week():
    """Built so a reserved slot IS taken and slots remain for filler, which is
    the only arrangement where a promoted week could be listed twice -- once
    as an alternative and again as next-best."""
    weeks = [week([Day.MON, Day.TUE, Day.WED, Day.THU, Day.FRI], start=s)
             for s in ("13:00", "13:15", "13:30", "13:45")]
    weeks.append(week([Day.MON], start="09:00"))   # earns a reserved slot

    picks = alternatives(weeks, limit=10)
    assert any(a.reason.startswith("best for") for a in picks), \
        "test is pointless unless a slot is actually reserved"
    assert any(a.reason == "next best" for a in picks), \
        "test is pointless unless the filler pass also runs"

    shapes = [schedule_shape(a.option.schedule) for a in picks]
    assert len(shapes) == len(set(shapes))


def test_reserved_slots_come_before_filler():
    """Greedy by largest gain, so the most different option is the one the
    student sees first, not buried under next-bests."""
    weeks = [week([Day.MON, Day.TUE, Day.WED, Day.THU, Day.FRI], start=s)
             for s in ("13:00", "13:15", "13:30", "13:45")]
    weeks.append(week([Day.MON], start="09:00"))
    reasons = [a.reason for a in alternatives(weeks)]
    assert reasons[0] == "best overall"
    filler = [i for i, r in enumerate(reasons) if r == "next best"]
    reserved = [i for i, r in enumerate(reasons) if r.startswith("best for")]
    assert not filler or not reserved or min(filler) > max(reserved)


def test_limit_is_respected():
    weeks = [week([d]) for d in (Day.MON, Day.TUE, Day.WED, Day.THU, Day.FRI)]
    assert len(alternatives(weeks, limit=3)) == 3


def test_a_single_week_yields_a_single_row():
    picks = alternatives([week([Day.MON])])
    assert len(picks) == 1 and picks[0].reason == "best overall"


def test_no_schedules_yields_no_alternatives():
    assert diverse_top([]) == []
