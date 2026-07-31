"""Campus inference and the commute constraint.

Campus is not published by the class-search API (no campus, location, or
building field on any row), so it is inferred from NYU's school code. That makes
it an assumption worth pinning, like the recitation linkage.
"""
import pytest

from campuses import (
    campus_of, school_of, campuses_in, WASHINGTON_SQUARE, BROOKLYN,
)
from constraints import CommuteBuffer, satisfying
from models import Course, Day, Meeting, Section, SectionKind, hhmm


def section(course, day, start, end, kind=SectionKind.LECTURE):
    return Section(f"{course}-{start}", course, kind, None,
                   [Meeting(day, hhmm(start), hhmm(end))])


# --- campus inference --------------------------------------------------------

def test_school_code_is_the_half_after_the_hyphen():
    assert school_of("CSCI-UA 102") == "UA"
    assert school_of("EG-UY 1004") == "UY"
    assert school_of("MA-GY 6213") == "GY"


@pytest.mark.parametrize("code", ["CSCI-UA 102", "MATH-UA 121", "EXPOS-UA 1",
                                  "MATH-GA 2010"])
def test_washington_square_schools(code):
    assert campus_of(code) == WASHINGTON_SQUARE


@pytest.mark.parametrize("code", ["EG-UY 1004", "MA-GY 6213", "CS-UY 1114"])
def test_tandon_schools_are_brooklyn(code):
    """Tandon's school codes end in Y -- UY undergraduate, GY graduate."""
    assert campus_of(code) == BROOKLYN


def test_unknown_school_defaults_to_washington_square():
    """Documented limitation: NYU has sites beyond these two, and anything
    unrecognized lands in Manhattan rather than erroring."""
    assert campus_of("XYZW-QQ 1") == WASHINGTON_SQUARE


def test_campuses_in_reports_every_campus_present():
    courses = [Course("CSCI-UA 102", "", []), Course("EG-UY 1004", "", [])]
    assert campuses_in(courses) == {WASHINGTON_SQUARE, BROOKLYN}


def test_single_campus_course_list_reports_one():
    courses = [Course("CSCI-UA 102", "", []), Course("MATH-UA 121", "", [])]
    assert campuses_in(courses) == {WASHINGTON_SQUARE}


# --- the commute constraint --------------------------------------------------

def test_back_to_back_across_campuses_is_rejected():
    """These do not overlap, so has_conflict allows them -- but attending both
    would mean being in Manhattan and Brooklyn at the same moment."""
    schedule = [section("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                section("EG-UY 1004", Day.MON, "14:00", "15:00")]
    assert CommuteBuffer(30).allows(schedule) is False


def test_enough_travel_time_is_allowed():
    schedule = [section("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                section("EG-UY 1004", Day.MON, "14:30", "15:30")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_exactly_the_buffer_is_enough():
    schedule = [section("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                section("EG-UY 1004", Day.MON, "13:30", "14:30")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_back_to_back_on_the_same_campus_is_fine():
    """Walking between Washington Square buildings is not a commute."""
    schedule = [section("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                section("MATH-UA 121", Day.MON, "14:00", "15:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_travel_before_the_first_class_is_not_constrained():
    """Getting to campus in the morning is the student's own time. A single
    Brooklyn class at 09:00 must never be rejected for having no run-up."""
    schedule = [section("EG-UY 1004", Day.MON, "09:00", "10:00"),
                section("CSCI-UA 102", Day.TUE, "09:00", "10:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_travel_after_the_last_class_is_not_constrained():
    schedule = [section("CSCI-UA 102", Day.MON, "09:00", "10:00"),
                section("EG-UY 1004", Day.MON, "10:30", "23:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_days_are_evaluated_independently():
    """A Manhattan class Monday afternoon and a Brooklyn class Tuesday morning
    are unrelated, however close the clock times look."""
    schedule = [section("CSCI-UA 102", Day.MON, "16:00", "17:00"),
                section("EG-UY 1004", Day.TUE, "09:00", "10:00")]
    assert CommuteBuffer(30).allows(schedule) is True


def test_the_middle_of_three_classes_is_checked():
    """The violation is between the second and third, not the first pair."""
    schedule = [section("CSCI-UA 102", Day.MON, "09:00", "10:00"),
                section("CSCI-UA 101", Day.MON, "10:30", "11:30"),
                section("EG-UY 1004", Day.MON, "11:35", "12:35")]
    assert CommuteBuffer(30).allows(schedule) is False


def test_recitations_and_labs_count_as_travel_too():
    """A Tandon lab is as much a place you must physically be as a lecture."""
    schedule = [section("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                section("EG-UY 1004", Day.MON, "13:10", "16:00",
                        kind=SectionKind.LAB)]
    assert CommuteBuffer(30).allows(schedule) is False


def test_zero_buffer_allows_everything():
    schedule = [section("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                section("EG-UY 1004", Day.MON, "14:00", "15:00")]
    assert CommuteBuffer(0).allows(schedule) is True


def test_a_custom_campus_resolver_can_replace_nyus():
    """The constraint takes campus_of as a parameter so it is not NYU-only."""
    everything_is_far = CommuteBuffer(30, campus_of=lambda code: code)
    schedule = [section("CSCI-UA 102", Day.MON, "12:00", "13:00"),
                section("MATH-UA 121", Day.MON, "13:00", "14:00")]
    assert everything_is_far.allows(schedule) is False


def test_filtering_uses_the_constraint_like_any_other():
    ok = [section("CSCI-UA 102", Day.MON, "09:00", "10:00"),
          section("EG-UY 1004", Day.MON, "11:00", "12:00")]
    bad = [section("CSCI-UA 102", Day.MON, "09:00", "10:00"),
           section("EG-UY 1004", Day.MON, "10:05", "11:00")]
    assert satisfying([ok, bad], [CommuteBuffer(30)]) == [ok]


# --- diagnostics -------------------------------------------------------------

def test_violations_name_the_day_campuses_and_gap():
    schedule = [section("CSCI-UA 102", Day.MON, "12:30", "14:00"),
                section("EG-UY 1004", Day.MON, "14:10", "15:00")]
    problems = CommuteBuffer(30).violations(schedule)
    assert len(problems) == 1
    assert "MON" in problems[0]
    assert "10 min apart" in problems[0]
    assert "needs 30" in problems[0]


def test_explanation_names_both_campuses():
    courses = [Course("CSCI-UA 102", "", []), Course("EG-UY 1004", "", [])]
    text = CommuteBuffer(30).explain(courses)
    assert WASHINGTON_SQUARE in text and BROOKLYN in text
    assert "smaller buffer" in text
