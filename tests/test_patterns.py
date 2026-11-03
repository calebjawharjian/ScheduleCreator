"""Course codes and level wildcards: what a student types to name a course."""
import pytest

from patterns import CoursePattern


def test_an_exact_code_matches_only_itself():
    p = CoursePattern.parse("CSCI-UA 102")
    assert not p.is_wildcard
    assert p.matches("CSCI-UA 102")
    assert not p.matches("CSCI-UA 101")


def test_a_level_wildcard_matches_that_level_only():
    p = CoursePattern.parse("CSCI-UA 4xx")
    assert p.is_wildcard
    assert p.matches("CSCI-UA 472") and p.matches("CSCI-UA 480")
    assert not p.matches("CSCI-UA 310")


def test_each_x_is_exactly_one_digit():
    """Digit-for-digit, not numeric: '4xx' must not reach a four-digit 4100,
    and the same rule has to serve Tandon's four-digit numbers."""
    assert not CoursePattern.parse("CSCI-UA 4xx").matches("CSCI-UA 4100")
    assert CoursePattern.parse("CS-UY 2xxx").matches("CS-UY 2124")
    assert not CoursePattern.parse("CS-UY 2xxx").matches("CS-UY 212")


def test_the_subject_must_match_exactly():
    assert not CoursePattern.parse("CSCI-UA 4xx").matches("CSCI-GA 472")


def test_case_and_spacing_are_forgiven():
    assert CoursePattern.parse("  csci-ua 4XX ") == CoursePattern.parse("CSCI-UA 4xx")


def test_a_short_number_is_an_exact_code_never_a_prefix():
    """EXPOS-UA 1 is a real course. 'CSCI-UA 4' therefore means course 4, not
    'anything starting with 4' -- a wildcard is only ever spelled with x."""
    p = CoursePattern.parse("EXPOS-UA 1")
    assert not p.is_wildcard
    assert p.matches("EXPOS-UA 1") and not p.matches("EXPOS-UA 13")


@pytest.mark.parametrize("text", ["CSCI-UA", "4xx", "CSCI-UA xxx", "CSCI-UA 4?x",
                                  "MATH 121"])
def test_anything_else_is_refused_not_guessed(text):
    with pytest.raises(ValueError):
        CoursePattern.parse(text)
