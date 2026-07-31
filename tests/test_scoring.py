import pytest

from models import Day, Meeting, Section, SectionKind, hhmm
from scoring import (
    Preferences, score_schedule, rank_schedules, compare_to_ideal,
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
