"""Campus tagging and the commute constraint.

Campus comes from NYU's own Location filter, not from the course code. An
earlier version inferred it from the school code (UY/GY = Brooklyn) and was
wrong in the dangerous direction: NYU runs 9 MATH-UA sections in Brooklyn for
Fall 2026, so that inference silently permitted impossible schedules.

Campus is per-section. MATH-UA 140 has 32 sections; 021 and 022 are in
Brooklyn and the other 30 are at Washington Square.
"""
import pytest

from campuses import (
    campuses_in, BROOKLYN_CAMP_CODES, WASHINGTON_SQUARE, BROOKLYN,
)
from constraints import CommuteBuffer, satisfying, best_effort
from models import Course, Day, Meeting, Section, SectionKind, hhmm


def section(course, day, start, end, campus, kind=SectionKind.LECTURE):
    return Section(f"{course}-{start}", course, kind, None,
                   [Meeting(day, hhmm(start), hhmm(end))], campus=campus)


def ws(course, day, start, end, **kw):
    return section(course, day, start, end, WASHINGTON_SQUARE, **kw)


def bk(course, day, start, end, **kw):
    return section(course, day, start, end, BROOKLYN, **kw)


# --- campus data -------------------------------------------------------------

def test_brooklyn_filter_codes_match_the_class_search_dropdown():
    """Pinned because they come from NYU's own `select#crit-camp` options; a
    silent change here would stop tagging anything as Brooklyn."""
    assert BROOKLYN_CAMP_CODES == "WS@BRKLN,WS@INDUS"


def test_campuses_in_reads_sections_not_course_codes():
    """The case that broke the old design: a UA-coded course with a Brooklyn
    section means the schedule really does span two campuses."""
    math = Course("MATH-UA 140", "Calculus", [
        ws("MATH-UA 140", Day.MON, "09:00", "10:00"),
        bk("MATH-UA 140", Day.TUE, "11:00", "12:00"),
    ])
    assert campuses_in([math]) == {WASHINGTON_SQUARE, BROOKLYN}


def test_untagged_sections_contribute_no_campus():
    course = Course("XX 1", "", [Section("S", "XX 1", SectionKind.LECTURE, None, [])])
    assert campuses_in([course]) == set()


# --- the commute constraint --------------------------------------------------

def test_back_to_back_across_campuses_is_rejected():
    """These do not overlap, so has_conflict allows them -- but attending both
    would mean being in Manhattan and Brooklyn at the same moment."""
    schedule = [ws("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                bk("EG-UY 1004", Day.MON, "14:00", "15:00")]
    assert CommuteBuffer(30).allows(schedule) is False


def test_a_brooklyn_section_of_a_ua_course_is_treated_as_brooklyn():
    """The regression. Both are MATH-UA, so any course-code-based rule would
    call this a single-campus day and allow a 10-minute hop to Brooklyn."""
    schedule = [ws("MATH-UA 121", Day.MON, "09:00", "10:00"),
                bk("MATH-UA 140", Day.MON, "10:10", "11:00")]
    assert CommuteBuffer(30).allows(schedule) is False


def test_enough_travel_time_is_allowed():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                bk("EG-UY 1004", Day.MON, "14:30", "15:30")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_exactly_the_buffer_is_enough():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                bk("EG-UY 1004", Day.MON, "13:30", "14:30")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_back_to_back_on_the_same_campus_is_fine():
    """Walking between Washington Square buildings is not a commute."""
    schedule = [ws("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                ws("MATH-UA 121", Day.MON, "14:00", "15:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_travel_before_the_first_class_is_not_constrained():
    """Getting to campus in the morning is the student's own time."""
    schedule = [bk("EG-UY 1004", Day.MON, "09:00", "10:00"),
                ws("CSCI-UA 102", Day.TUE, "09:00", "10:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_travel_after_the_last_class_is_not_constrained():
    schedule = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
                bk("EG-UY 1004", Day.MON, "10:30", "23:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_days_are_evaluated_independently():
    schedule = [ws("CSCI-UA 102", Day.MON, "16:00", "17:00"),
                bk("EG-UY 1004", Day.TUE, "09:00", "10:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_the_middle_of_three_classes_is_checked():
    schedule = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
                ws("CSCI-UA 101", Day.MON, "10:30", "11:30"),
                bk("EG-UY 1004", Day.MON, "11:35", "12:35")]
    assert CommuteBuffer(30).allows(schedule) is False


def test_labs_count_as_travel_too():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                bk("EG-UY 1004", Day.MON, "13:10", "16:00", kind=SectionKind.LAB)]
    assert CommuteBuffer(30).allows(schedule) is False


def test_untagged_sections_impose_no_travel_time():
    """An online class has nowhere to travel to, so it must not look like a
    third location wedged between two Manhattan classes."""
    online = Section("O", "XX 1", SectionKind.LECTURE, None,
                     [Meeting(Day.MON, hhmm("10:00"), hhmm("11:00"))], campus=None)
    schedule = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
                online,
                ws("MATH-UA 121", Day.MON, "11:00", "12:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_zero_buffer_allows_everything():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                bk("EG-UY 1004", Day.MON, "14:00", "15:00")]
    assert CommuteBuffer(0).allows(schedule) is True


def test_a_custom_resolver_can_replace_the_section_tag():
    far = CommuteBuffer(30, campus_of=lambda s: s.course_code)
    schedule = [ws("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                ws("MATH-UA 121", Day.MON, "13:00", "14:00")]
    assert far.allows(schedule) is False


# --- penalty and best fit ----------------------------------------------------

def test_penalty_is_zero_when_satisfied():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                bk("EG-UY 1004", Day.MON, "13:30", "14:30")]
    assert CommuteBuffer(30).penalty(schedule) == 0.0


def test_penalty_counts_minutes_short():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                bk("EG-UY 1004", Day.MON, "13:10", "14:10")]
    assert CommuteBuffer(30).penalty(schedule) == 20.0


def test_penalty_accumulates_across_transitions():
    schedule = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
                bk("EG-UY 1004", Day.MON, "10:10", "11:00"),   # 20 short
                ws("MATH-UA 121", Day.MON, "11:05", "12:00")]  # 25 short
    assert CommuteBuffer(30).penalty(schedule) == 45.0


def test_best_effort_prefers_full_satisfaction():
    good = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
            bk("EG-UY 1004", Day.MON, "11:00", "12:00")]
    bad = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
           bk("EG-UY 1004", Day.MON, "10:05", "11:00")]
    kept, compromised = best_effort([good, bad], [], [CommuteBuffer(30)])
    assert kept == [good]
    assert compromised == []


def test_best_effort_returns_closest_misses_when_nothing_fits():
    """The point: a student with no commuting-friendly option still has to
    register for something, so returning the near-misses beats returning
    nothing -- but the compromise is reported, never hidden."""
    near = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
            bk("EG-UY 1004", Day.MON, "10:25", "11:00")]   # 5 short
    far = [ws("CSCI-UA 102", Day.MON, "09:00", "10:00"),
           bk("EG-UY 1004", Day.MON, "10:01", "11:00")]    # 29 short
    kept, compromised = best_effort([near, far], [], [CommuteBuffer(30)])
    assert kept == [near]
    assert len(compromised) == 1
    assert isinstance(compromised[0], CommuteBuffer)


def test_best_effort_never_relaxes_a_required_professor():
    """Tiering: travel time gives way, a stated non-negotiable does not."""
    from constraints import RequiredInstructor

    bari = Section("L1", "CSCI-UA 102", SectionKind.LECTURE, "Anasse Bari",
                   [Meeting(Day.MON, hhmm("09:00"), hhmm("10:00"))],
                   campus=WASHINGTON_SQUARE)
    korth = Section("L2", "CSCI-UA 102", SectionKind.LECTURE, "Evan Korth",
                    [Meeting(Day.MON, hhmm("09:00"), hhmm("10:00"))],
                    campus=WASHINGTON_SQUARE)
    brooklyn = bk("EG-UY 1004", Day.MON, "10:05", "11:00")

    kept, compromised = best_effort(
        [[bari, brooklyn], [korth, brooklyn]],
        [RequiredInstructor("CSCI-UA 102", "Anasse Bari")],
        [CommuteBuffer(30)])

    assert kept == [[bari, brooklyn]]      # the professor survived
    assert len(compromised) == 1           # the commute gave way


def test_best_effort_reports_nothing_when_a_hard_requirement_fails():
    from constraints import RequiredInstructor

    korth = Section("L2", "CSCI-UA 102", SectionKind.LECTURE, "Evan Korth", [],
                    campus=WASHINGTON_SQUARE)
    kept, compromised = best_effort(
        [[korth]], [RequiredInstructor("CSCI-UA 102", "Anasse Bari")], [])
    assert kept == []
    assert compromised == []


# --- diagnostics -------------------------------------------------------------

def test_violations_name_the_day_campuses_and_gap():
    schedule = [ws("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                bk("EG-UY 1004", Day.MON, "14:10", "15:00")]
    problems = CommuteBuffer(30).violations(schedule)
    assert len(problems) == 1
    assert "MON" in problems[0]
    assert "10 min apart" in problems[0]
    assert "needs 30" in problems[0]


def test_explanation_names_both_campuses():
    courses = [Course("CSCI-UA 102", "", [ws("CSCI-UA 102", Day.MON, "09:00", "10:00")]),
               Course("EG-UY 1004", "", [bk("EG-UY 1004", Day.MON, "09:00", "10:00")])]
    text = CommuteBuffer(30).explain(courses)
    assert WASHINGTON_SQUARE in text and BROOKLYN in text
    assert "smaller buffer" in text
