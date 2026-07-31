"""Hard constraints.

The motivating case is real: a student wanted one specific CSCI-UA professor,
but that professor taught on Friday, which was not a preferred day. Weighted
scoring buried him -- correctly, by its own logic, which is the problem. A
requirement is not a very large preference; it is a filter.
"""
import pytest

from models import Course, Day, Meeting, Section, SectionKind, hhmm
from constraints import (
    Constraint, RequiredInstructor, parse_requirement, satisfying,
    section_survival, explain_unsatisfiable,
)


def lecture(sid, course, instructor, day=Day.MON, start="12:00", end="13:00"):
    return Section(sid, course, SectionKind.LECTURE, instructor,
                   [Meeting(day, hhmm(start), hhmm(end))])


def recitation(sid, course, parent, day=Day.FRI):
    return Section(sid, course, SectionKind.RECITATION, None,
                   [Meeting(day, hhmm("15:00"), hhmm("16:00"))],
                   parent_section_id=parent)


BARI = lecture("L1", "CSCI-UA 102", "Anasse Bari", Day.FRI, "14:00", "16:30")
KORTH = lecture("L2", "CSCI-UA 102", "Evan Korth", Day.MON)


# --- parsing -----------------------------------------------------------------

def test_parses_course_qualified_requirement():
    req = parse_requirement("CSCI-UA 102=Anasse Bari")
    assert req.course_code == "CSCI-UA 102"
    assert req.instructor == "Anasse Bari"


def test_parsing_tolerates_surrounding_whitespace():
    req = parse_requirement("  CSCI-UA 102 = Anasse Bari  ")
    assert req.course_code == "CSCI-UA 102"
    assert req.instructor == "Anasse Bari"


@pytest.mark.parametrize("bad", ["Anasse Bari", "CSCI-UA 102=", "=Anasse Bari", ""])
def test_malformed_requirements_are_rejected(bad):
    """Fail loudly: a requirement that silently parsed to nothing would leave
    the user believing a non-negotiable was applied when it wasn't."""
    with pytest.raises(ValueError):
        parse_requirement(bad)


# --- filtering ---------------------------------------------------------------

def test_schedule_with_the_required_instructor_is_kept():
    req = RequiredInstructor("CSCI-UA 102", "Anasse Bari")
    assert req.allows([BARI]) is True


def test_schedule_without_the_required_instructor_is_rejected():
    req = RequiredInstructor("CSCI-UA 102", "Anasse Bari")
    assert req.allows([KORTH]) is False


def test_matching_ignores_case_and_extra_whitespace():
    req = RequiredInstructor("csci-ua 102", "anasse   bari")
    assert req.allows([BARI]) is True


def test_requirement_is_silent_about_courses_it_does_not_name():
    """A CSCI requirement must not reject a schedule for having Math in it."""
    req = RequiredInstructor("CSCI-UA 102", "Anasse Bari")
    math = lecture("M1", "MATH-UA 121", "Fanny Shum")
    assert req.allows([BARI, math]) is True


def test_requirement_applies_to_lectures_not_recitations():
    """Recitations are usually staff-taught and their instructor is rarely
    published, so requiring a name against them would reject everything."""
    req = RequiredInstructor("CSCI-UA 102", "Anasse Bari")
    assert req.allows([BARI, recitation("R1", "CSCI-UA 102", "L1")]) is True


def test_satisfying_filters_a_list_of_schedules():
    req = RequiredInstructor("CSCI-UA 102", "Anasse Bari")
    kept = satisfying([[BARI], [KORTH], [BARI, KORTH]], [req])
    assert [KORTH] not in kept
    assert [BARI] in kept


def test_no_constraints_keeps_everything_unchanged():
    schedules = [[BARI], [KORTH]]
    assert satisfying(schedules, []) is schedules


def test_multiple_requirements_all_have_to_hold():
    shum = lecture("M1", "MATH-UA 121", "Fanny Shum")
    stepp = lecture("M2", "MATH-UA 121", "Elizabeth Stepp")
    reqs = [RequiredInstructor("CSCI-UA 102", "Anasse Bari"),
            RequiredInstructor("MATH-UA 121", "Fanny Shum")]

    assert satisfying([[BARI, shum]], reqs) == [[BARI, shum]]
    assert satisfying([[BARI, stepp]], reqs) == []
    assert satisfying([[KORTH, shum]], reqs) == []


def test_a_requirement_beats_a_scoring_preference():
    """The whole point. Bari meets Friday; Friday is not preferred; weighted
    scoring ranks him last. The constraint removes the alternatives entirely,
    so the preference never gets the chance to overrule it."""
    from scoring import Preferences, rank_schedules, DAYS, PROFESSOR

    prefs = Preferences(preferred_days={Day.MON}, time_start=hhmm("09:00"),
                        time_end=hhmm("18:00"), professor_ratings={},
                        weights={DAYS: 10, PROFESSOR: 1})
    schedules = [[BARI], [KORTH]]

    # Unconstrained, the Monday professor wins on the days factor.
    assert rank_schedules(schedules, prefs)[0][0] == [KORTH]

    # Constrained, he is not a candidate at all.
    constrained = satisfying(schedules, [RequiredInstructor("CSCI-UA 102", "Anasse Bari")])
    assert rank_schedules(constrained, prefs)[0][0] == [BARI]


# --- diagnostics -------------------------------------------------------------

@pytest.fixture
def courses():
    return [Course("CSCI-UA 102", "Data Structures", [BARI, KORTH])]


def test_section_survival_counts_only_the_constrained_course(courses):
    courses.append(Course("MATH-UA 121", "Calculus I",
                          [lecture("M1", "MATH-UA 121", "Fanny Shum")]))
    kept, total = section_survival(courses, RequiredInstructor("CSCI-UA 102", "Anasse Bari"))
    assert (kept, total) == (1, 2)


def test_explains_a_misspelled_professor(courses):
    lines = explain_unsatisfiable(courses, [RequiredInstructor("CSCI-UA 102", "Anass Bari")])
    assert "matched none" in lines[0]
    assert "spelling" in lines[0]


def test_explains_a_course_that_was_not_requested(courses):
    lines = explain_unsatisfiable(courses, [RequiredInstructor("PHYS-UA 11", "Someone")])
    assert "not requested" in lines[0]


def test_a_requirement_for_an_unrequested_course_is_vacuous(courses):
    """Documents why main.py treats this as a hard error rather than a warning.
    The constraint itself cannot reject anything -- it has no course to judge --
    so a typo'd course code would silently produce a ranking that ignores a
    non-negotiable. The check has to happen before filtering, not inside it."""
    stray = RequiredInstructor("CSCI-UA 1O2", "Anasse Bari")   # letter O
    assert stray.allows([KORTH]) is True
    assert satisfying([[BARI], [KORTH]], [stray]) == [[BARI], [KORTH]]


def test_explains_a_genuine_scheduling_conflict(courses):
    lines = explain_unsatisfiable(courses, [RequiredInstructor("CSCI-UA 102", "Anasse Bari")])
    assert "kept 1 of 2" in lines[0]
    assert "conflict" in lines[0]


# --- extensibility -----------------------------------------------------------

def test_a_new_constraint_needs_only_the_two_abstract_methods():
    """The seam exists so 'no Friday classes' is a new predicate rather than a
    new mechanism. This is that constraint, written in full."""

    class NoMeetingsOn(Constraint):
        def __init__(self, day):
            self.day = day

        def allows(self, schedule):
            return not any(m.day == self.day for s in schedule for m in s.meetings)

        def describe(self):
            return f"no classes on {self.day.name}"

    no_friday = NoMeetingsOn(Day.FRI)
    assert no_friday.allows([KORTH]) is True
    assert no_friday.allows([BARI]) is False
    assert satisfying([[BARI], [KORTH]], [no_friday]) == [[KORTH]]
