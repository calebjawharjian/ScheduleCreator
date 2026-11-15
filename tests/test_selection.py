"""Course selection: groups, counts, required vs optional, credit ranges.

Hand-built courses rather than recordings: these rules are pure combinatorics
over whole courses, and tiny synthetic catalogs make each rule's effect exact.
"""
import pytest

from models import Course, Section, SectionKind
from selection import (CourseRequest, CreditRange, SelectionError,
                       MAX_COMBINATIONS, resolve)


def course(code, credits=4.0, topic=None, schedulable=True):
    sections = ([Section(f"{code}-{topic}-L", code, SectionKind.LECTURE, None)]
                if schedulable else
                [Section(f"{code}-R", code, SectionKind.RECITATION, None)])
    return Course(code, code, sections, topic=topic, credits=credits)


def labels(selection):
    return [sorted(c.label for c in s) for s in selection.sets]


def req(text, required=True, count=1):
    return CourseRequest.of(text, required, count)


def test_parse_reads_a_count_prefix_without_touching_the_wildcard():
    r = CourseRequest.parse("2x csci-ua 4xx")
    assert (r.text, r.count, r.required) == ("CSCI-UA 4xx", 2, True)
    assert CourseRequest.parse("CSCI-UA 4xx").count == 1


def test_required_courses_all_appear_in_the_one_set():
    a, b = course("CSCI-UA 101"), course("CSCI-UA 102")
    sel = resolve([req("CSCI-UA 101"), req("CSCI-UA 102")], [a, b])
    assert labels(sel) == [["CSCI-UA 101", "CSCI-UA 102"]]


def test_a_required_wildcard_takes_exactly_its_count():
    pool = [course(f"CSCI-UA 47{i}") for i in range(4)]
    sel = resolve([req("CSCI-UA 4xx", count=2)], pool)
    assert len(sel.sets) == 6                      # 4 choose 2
    assert all(len(s) == 2 for s in sel.sets)


def test_an_optional_course_may_be_left_out():
    a, b = course("CSCI-UA 101"), course("MATH-UA 121")
    sel = resolve([req("CSCI-UA 101"), req("MATH-UA 121", required=False)], [a, b])
    assert labels(sel) == [["CSCI-UA 101"], ["CSCI-UA 101", "MATH-UA 121"]]


def test_an_optional_group_takes_anywhere_from_zero_to_its_count():
    base = course("MATH-UA 121")
    pool = [course(f"CSCI-UA 47{i}") for i in range(3)]
    sel = resolve([req("MATH-UA 121"), req("CSCI-UA 4xx", required=False, count=2)],
                  [base, *pool])
    sizes = sorted(len(s) for s in sel.sets)
    assert sizes == [1, 2, 2, 2, 3, 3, 3]          # +0, +1 (x3), +2 (x3)


def test_a_course_is_never_counted_twice():
    """Requiring CSCI-UA 473 and also one of CSCI-UA 4xx must not produce a
    set holding 473 twice -- it would be scheduled against itself."""
    pool = [course("CSCI-UA 472"), course("CSCI-UA 473")]
    sel = resolve([req("CSCI-UA 473"), req("CSCI-UA 4xx")], pool)
    assert labels(sel) == [["CSCI-UA 472", "CSCI-UA 473"]]


def test_the_same_set_reached_two_ways_is_scheduled_once():
    pool = [course("CSCI-UA 472"), course("CSCI-UA 473")]
    sel = resolve([req("CSCI-UA 47x"), req("CSCI-UA 4xx")], pool)
    assert labels(sel) == [["CSCI-UA 472", "CSCI-UA 473"]]


def test_topics_of_one_code_are_separate_choices():
    topics = [course("CSCI-UA 480", topic=t) for t in ("Graphics", "Vision")]
    sel = resolve([req("CSCI-UA 480")], topics)
    assert labels(sel) == [["CSCI-UA 480: Graphics"], ["CSCI-UA 480: Vision"]]


def test_the_credit_range_keeps_only_sets_inside_it():
    base = course("MATH-UA 121", credits=4)
    extra = [course("CSCI-UA 101", credits=4), course("CSCI-UA 2", credits=2)]
    sel = resolve([req("MATH-UA 121"), req("CSCI-UA 101", required=False),
                   req("CSCI-UA 2", required=False)],
                  [base, *extra], CreditRange(6, 8))
    totals = sorted(sum(c.credits for c in s) for s in sel.sets)
    assert totals == [6, 8]


def test_credits_are_counted_per_course_not_per_section():
    """Each course carries its credit value once, whatever its bundle size."""
    lab = Course("EG-UY 1004", "", [
        Section("L", "EG-UY 1004", SectionKind.LECTURE, None),
        Section("B", "EG-UY 1004", SectionKind.LAB, None, parent_section_id="L"),
        Section("R", "EG-UY 1004", SectionKind.RECITATION, None, parent_section_id="L"),
    ], credits=4)
    assert resolve([req("EG-UY 1004")], [lab], CreditRange(4, 4)).sets


def test_unknown_credits_under_a_range_are_refused_not_counted_as_zero():
    with pytest.raises(SelectionError, match="unknown"):
        resolve([req("MATH-UA 121")], [course("MATH-UA 121", credits=None)],
                CreditRange(high=16))


def test_unknown_credits_are_fine_without_a_range():
    assert resolve([req("MATH-UA 121")], [course("MATH-UA 121", credits=None)]).sets


def test_an_impossible_range_reports_the_possible_totals():
    with pytest.raises(SelectionError, match="4 to 4"):
        resolve([req("MATH-UA 121")], [course("MATH-UA 121")], CreditRange(12, 16))


def test_an_unschedulable_optional_candidate_is_skipped_and_named():
    """One seminar-only elective must not sink the whole wildcard."""
    good, bad = course("CSCI-UA 472"), course("CSCI-UA 473", schedulable=False)
    sel = resolve([req("CSCI-UA 4xx", required=False)], [good, bad])
    assert labels(sel) == [["CSCI-UA 472"]]
    assert sel.skipped == ["CSCI-UA 473"]


def test_an_unschedulable_required_course_still_fails_loudly():
    with pytest.raises(SelectionError, match="registrable"):
        resolve([req("CSCI-UA 473")], [course("CSCI-UA 473", schedulable=False)])


def test_one_unschedulable_topic_does_not_take_its_siblings_with_it():
    """Topics share a code; 'unschedulable' must go by course, not by code."""
    ok = course("CSCI-UA 480", topic="Graphics")
    stuck = course("CSCI-UA 480", topic="Vision", schedulable=False)
    sel = resolve([req("CSCI-UA 480")], [ok, stuck])
    assert labels(sel) == [["CSCI-UA 480: Graphics"]]


def test_a_required_wildcard_with_too_few_matches_fails():
    with pytest.raises(SelectionError, match="needs 3"):
        resolve([req("CSCI-UA 4xx", count=3)],
                [course("CSCI-UA 472"), course("CSCI-UA 473")])


def test_an_explosive_request_is_refused_before_any_work():
    pool = [course(f"CSCI-UA 4{i:02d}") for i in range(60)]
    with pytest.raises(SelectionError, match="too many"):
        resolve([req("CSCI-UA 4xx", count=4)], pool)   # 487,635 > MAX
    assert MAX_COMBINATIONS < 487_635
