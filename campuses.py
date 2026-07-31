"""Which campus a course meets on. NYU-specific, INFERRED from the course code.

The class-search API publishes no campus, location, or building field -- checked
across EG-UY and CSCI-UA rows for Fall 2026, all empty. So campus comes from
NYU's school code, the second half of the subject:

    CSCI-UA 102  -> UA -> Washington Square (Manhattan)
    EG-UY 1004   -> UY -> Brooklyn (Tandon)
    MA-GY 6213   -> GY -> Brooklyn (Tandon)

Tandon's school codes end in Y (UY undergraduate, GY graduate); the Washington
Square schools do not. That is a real NYU convention rather than a guess, but it
is still an inference from a naming scheme, so it is isolated here where it can
be corrected in one place -- and kept out of `constraints.py`, which stays
university-agnostic by taking a resolver instead of hardcoding this.

Known limitation: NYU has sites beyond these two (Shanghai, Abu Dhabi, and
scattered Manhattan buildings). Anything not recognized as Tandon is treated as
Washington Square, which is right for the overwhelming majority of course codes
a student combines but would need revisiting to model those sites properly.
"""
from __future__ import annotations

WASHINGTON_SQUARE = "Washington Square"
BROOKLYN = "Brooklyn (Tandon)"

# School codes that meet in Brooklyn rather than Washington Square.
BROOKLYN_SCHOOLS = {"UY", "GY"}


def school_of(course_code: str) -> str:
    """'CSCI-UA 102' -> 'UA'. The half of the subject after the hyphen."""
    subject = course_code.split()[0]
    _, _, school = subject.rpartition("-")
    return school.upper()


def campus_of(course_code: str) -> str:
    """Which campus a course meets on."""
    return BROOKLYN if school_of(course_code) in BROOKLYN_SCHOOLS else WASHINGTON_SQUARE


def campuses_in(courses) -> set[str]:
    """Every campus represented across these courses."""
    return {campus_of(course.code) for course in courses}
