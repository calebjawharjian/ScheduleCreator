"""Hard constraints: requirements a schedule must satisfy to be offered at all.

Distinct from `scoring.py`, and the distinction is the point. Weights express
*preferences* that trade off against one another -- that is what makes them
weights. No weight, however large, can express "must". Setting
`professor=1000` only makes every Anasse Bari schedule score highly; a
Friday-free schedule without him can still win on the other five factors, which
is exactly how a student who wanted one specific professor ended up seeing him
ranked near the bottom.

So a requirement is a filter applied *before* scoring, not a factor inside it.
Constraints remove candidates; scoring orders whatever survives.

Filtering also prunes hard. Requiring one instructor for one course cuts the
candidate space by roughly the number of alternative lecture sections, which
takes a real bite out of the combinatorial explosion on large courses.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import re

from models import Section, SectionKind


def _norm(text: str | None) -> str:
    """Casefold and collapse whitespace, so 'anasse  bari' matches 'Anasse Bari'."""
    return re.sub(r"\s+", " ", text or "").strip().casefold()


class Constraint(ABC):
    """A yes/no test a candidate schedule must pass.

    Deliberately a predicate over a finished schedule rather than something
    woven into bundle generation: it keeps each constraint independently
    understandable and testable, and lets new kinds (no Friday meetings, nothing
    before 10am) be added without touching the scheduler.
    """

    @abstractmethod
    def allows(self, schedule: list[Section]) -> bool:
        """True if this schedule satisfies the constraint."""
        ...

    @abstractmethod
    def describe(self) -> str:
        """Human-readable form, used when explaining an impossible request."""
        ...

    def permits_section(self, section: Section) -> bool:
        """Whether this section could appear in any satisfying schedule.

        Used only to diagnose an unsatisfiable request ("kept 4 of 17
        sections"). Defaults to permissive, since most constraints are about
        combinations rather than individual sections.
        """
        return True

    def constrains_course(self, course_code: str) -> bool:
        """Whether this constraint has anything to say about a course. Lets the
        diagnostics count only the sections a constraint actually judges."""
        return True


class RequiredInstructor(Constraint):
    """Pin one course to one instructor.

    Matches against lectures only, mirroring `_score_professor`: recitations and
    labs are usually staff-taught, and their instructor is rarely published, so
    requiring a name there would reject everything.
    """

    def __init__(self, course_code: str, instructor: str):
        self.course_code = course_code
        self.instructor = instructor
        self._code = _norm(course_code)
        self._name = _norm(instructor)

    def allows(self, schedule: list[Section]) -> bool:
        lectures = [s for s in schedule
                    if s.kind == SectionKind.LECTURE and _norm(s.course_code) == self._code]
        if not lectures:
            # The course isn't in this schedule at all, so there is nothing to
            # constrain. Whether that course was requested is checked earlier.
            return True
        return any(_norm(s.instructor) == self._name for s in lectures)

    def permits_section(self, section: Section) -> bool:
        if _norm(section.course_code) != self._code:
            return True
        if section.kind != SectionKind.LECTURE:
            return True
        return _norm(section.instructor) == self._name

    def constrains_course(self, course_code: str) -> bool:
        return _norm(course_code) == self._code

    def describe(self) -> str:
        return f"{self.course_code} taught by {self.instructor}"


def parse_requirement(text: str) -> RequiredInstructor:
    """'CSCI-UA 102=Anasse Bari' -> RequiredInstructor.

    Raises ValueError with a usable message; callers turn that into either an
    argparse error or a re-prompt.
    """
    course_code, sep, instructor = text.partition("=")
    if not sep or not course_code.strip() or not instructor.strip():
        raise ValueError(
            f"expected COURSE=Professor, got {text!r} "
            f'(e.g. "CSCI-UA 102=Anasse Bari")')
    return RequiredInstructor(course_code.strip(), instructor.strip())


def satisfying(schedules: list[list[Section]],
               constraints: list[Constraint]) -> list[list[Section]]:
    """Every schedule that passes all constraints."""
    if not constraints:
        return schedules
    return [s for s in schedules if all(c.allows(s) for c in constraints)]


def section_survival(courses, constraint: Constraint) -> tuple[int, int]:
    """(kept, total) lecture sections under one constraint, for diagnostics."""
    lectures = [s for course in courses for s in course.sections
                if s.kind == SectionKind.LECTURE]
    considered = [s for s in lectures if constraint.constrains_course(s.course_code)]
    kept = [s for s in considered if constraint.permits_section(s)]
    return len(kept), len(considered)


def explain_unsatisfiable(courses, constraints: list[Constraint]) -> list[str]:
    """Why nothing survived, per constraint.

    Reported rather than silently relaxed: a requirement the user called
    non-negotiable should never be quietly dropped just to produce results.
    """
    lines: list[str] = []
    for constraint in constraints:
        kept, total = section_survival(courses, constraint)
        if total == 0:
            lines.append(f"  {constraint.describe()}: that course was not requested")
        elif kept == 0:
            lines.append(f"  {constraint.describe()}: matched none of "
                         f"{total} lecture sections -- check the spelling, or "
                         f"they may not be teaching it this term")
        else:
            lines.append(f"  {constraint.describe()}: kept {kept} of {total} "
                         f"lecture sections, all of which conflict with your "
                         f"other courses")
    return lines
