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

    def explain(self, courses) -> str:
        """Why this constraint might have eliminated everything. Overridden
        where a constraint can say something more useful than its own name."""
        return self.describe()

    def penalty(self, schedule: list[Section]) -> float:
        """How badly this schedule misses, 0.0 when satisfied.

        Only consulted when nothing satisfies everything and a best-fit answer
        is better than no answer. The default is all-or-nothing; constraints
        with a natural magnitude (minutes short) should override it so
        near-misses can be ranked ahead of distant ones.
        """
        return 0.0 if self.allows(schedule) else 1.0


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

    def explain(self, courses) -> str:
        kept, total = section_survival(courses, self)
        if total == 0:
            return f"{self.describe()}: that course was not requested"
        if kept == 0:
            return (f"{self.describe()}: matched none of {total} lecture "
                    f"sections -- check the spelling, or they may not be "
                    f"teaching it this term")
        return (f"{self.describe()}: kept {kept} of {total} lecture sections, "
                f"none of which fit alongside your other courses")

    def describe(self) -> str:
        return f"{self.course_code} taught by {self.instructor}"


class CommuteBuffer(Constraint):
    """Require travel time between consecutive classes on different campuses.

    A Washington Square class ending at 14:00 and a Brooklyn class starting at
    14:00 do not *conflict* -- the intervals don't overlap, so `has_conflict`
    is happy -- but no one can attend both. That makes this a correctness
    filter rather than a preference, the same category as a bundle missing its
    required lab.

    Only *between* classes. The trip to campus before the first class and home
    after the last one is the student's own time, not a scheduling problem, so
    only consecutive pairs within a day are checked.

    Reads `section.campus`, which the provider fills in from the university's
    own data. Campus is per-section and cannot be derived from the course code:
    NYU runs some Washington Square-coded courses in Brooklyn. A section with no
    campus (online, abroad, unknown) imposes no travel time, since there is
    nowhere to travel to.

    `campus_of` is overridable so any provider that tags sections differently
    can still use this.
    """

    def __init__(self, minutes: int = 30, campus_of=None):
        self.minutes = minutes
        self._campus_of = campus_of or (lambda section: section.campus)

    def _day_timeline(self, schedule: list[Section]):
        """Meetings grouped by day as (meeting, campus), earliest first.

        Sections without a campus are dropped rather than treated as a distinct
        location, so an online class between two Manhattan classes never looks
        like a round trip.
        """
        by_day: dict = {}
        for section in schedule:
            campus = self._campus_of(section)
            if not campus:
                continue
            for meeting in section.meetings:
                by_day.setdefault(meeting.day, []).append((meeting, campus))
        for day in by_day:
            by_day[day].sort(key=lambda pair: pair[0].start)
        return by_day

    def allows(self, schedule: list[Section]) -> bool:
        for timeline in self._day_timeline(schedule).values():
            for (earlier, from_campus), (later, to_campus) in zip(timeline, timeline[1:]):
                if from_campus == to_campus:
                    continue
                if later.start - earlier.end < self.minutes:
                    return False
        return True

    def violations(self, schedule: list[Section]) -> list[str]:
        """Which transitions fail, for explaining an impossible request."""
        problems = []
        for day, timeline in sorted(self._day_timeline(schedule).items()):
            for (earlier, a), (later, b) in zip(timeline, timeline[1:]):
                if a == b:
                    continue
                gap = later.start - earlier.end
                if gap < self.minutes:
                    problems.append(
                        f"{day.name}: {a} ends {earlier.end // 60:02d}:"
                        f"{earlier.end % 60:02d}, {b} starts "
                        f"{later.start // 60:02d}:{later.start % 60:02d} "
                        f"({gap} min apart, needs {self.minutes})")
        return problems

    def penalty(self, schedule: list[Section]) -> float:
        """Total minutes of travel time this schedule is short by.

        Lets a best-fit fallback rank near-misses: a schedule 5 minutes short
        on one transition is far more usable than one 25 minutes short on
        three, and both beat nothing at all.
        """
        shortfall = 0
        for timeline in self._day_timeline(schedule).values():
            for (earlier, a), (later, b) in zip(timeline, timeline[1:]):
                if a == b:
                    continue
                gap = later.start - earlier.end
                if gap < self.minutes:
                    shortfall += self.minutes - gap
        return float(shortfall)

    def explain(self, courses) -> str:
        spanned = sorted({s.campus for c in courses for s in c.sections if s.campus})
        return (f"{self.describe()}: your courses span {' and '.join(spanned)}, "
                f"and no combination leaves {self.minutes} minutes to travel "
                f"between back-to-back classes. Try a smaller buffer, or drop a "
                f"course from one campus.")

    def describe(self) -> str:
        return f"{self.minutes} minutes between campuses"


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


def best_effort(schedules: list[list[Section]],
                required: list[Constraint],
                preferred: list[Constraint]):
    """Satisfy everything if possible; otherwise return the closest misses.

    Returns `(schedules, compromised)`, where `compromised` lists the preferred
    constraints that had to give. Empty means nothing was compromised.

    Two tiers, because not all requirements are equally negotiable. A named
    professor is a stated preference the user called non-negotiable, so it is
    never relaxed -- if it cannot be met, that is a real dead end worth
    reporting. Travel time is physical reality, but a student with no
    alternative still has to register for something, so returning the schedules
    that come closest beats returning nothing.

    Among near-misses, minimum total penalty wins, so a schedule five minutes
    short on one transition ranks ahead of one half an hour short on three.
    Scoring then orders whatever comes back, as always.
    """
    eligible = satisfying(schedules, required)
    if not eligible:
        return [], []

    strict = satisfying(eligible, preferred)
    if strict or not preferred:
        return strict, []

    penalties = [(sum(c.penalty(s) for c in preferred), s) for s in eligible]
    lowest = min(cost for cost, _ in penalties)
    closest = [s for cost, s in penalties if cost == lowest]
    compromised = [c for c in preferred
                   if any(not c.allows(s) for s in closest)]
    return closest, compromised


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
    Each constraint explains itself, since "no professor by that name" and
    "not enough travel time" have entirely different fixes.
    """
    return [f"  {c.explain(courses)}" for c in constraints]
