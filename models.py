from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum


class Day(IntEnum):
    MON = 0
    TUE = 1
    WED = 2
    THU = 3
    FRI = 4
    SAT = 5
    SUN = 6


class SectionKind(IntEnum):
    LECTURE = 0
    RECITATION = 1
    LAB = 2


def hhmm(text: str) -> int:
    """Convert a '13:30' clock string to minutes since midnight (810)."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class Meeting:
    day: Day
    start: int   # minutes since midnight
    end: int     # minutes since midnight

    def overlaps(self, other: Meeting) -> bool:
        if self.day != other.day:
            return False
        # Two intervals overlap iff each starts before the other ends.
        return self.start < other.end and other.start < self.end


@dataclass
class Section:
    section_id: str            # globally unique, e.g. "MATH-UA-121-001"
    course_code: str           # "MATH-UA 121"
    kind: SectionKind
    instructor: str | None
    meetings: list[Meeting] = field(default_factory=list)
    parent_section_id: str | None = None   # set on a recitation/lab tied to a lecture
    is_open: bool = True
    section_no: str | None = None   # '001', for joining to department listings


@dataclass
class Course:
    code: str                  # "MATH-UA 121"
    title: str                 # "Calculus I"
    sections: list[Section] = field(default_factory=list)

    def lectures(self) -> list[Section]:
        return [s for s in self.sections if s.kind == SectionKind.LECTURE]

    def recitations_for(self, lecture_id: str) -> list[Section]:
        return [
            s for s in self.sections
            if s.kind != SectionKind.LECTURE and s.parent_section_id == lecture_id
        ]