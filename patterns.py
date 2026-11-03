"""What a student types to name a course: an exact code or a level wildcard.

'CSCI-UA 102' names one course. 'CSCI-UA 4xx' names every CSCI-UA course whose
catalog number is three digits starting with 4 -- each `x` stands for exactly
one digit, so 'CS-UY 2xxx' works the same way for four-digit Tandon numbers.

Matching is digit-for-digit rather than numeric (400 <= n < 500) on purpose:
'4xx' must not match a four-digit '4100', and the rule has to work for any
digit count without knowing a school's numbering in advance.

Lives outside nyu_provider because nothing here is NYU-specific: a pattern is
'subject + number', which any provider can filter its catalog with.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# 'CSCI-UA 4xx' -> ('CSCI-UA', '4xx'). Subjects are LETTERS-LETTERS, as in
# every NYU code seen so far ('CSCI-UA', 'CM-UY', 'EXPOS-UA').
PATTERN_RE = re.compile(r"([A-Z]+-[A-Z]+)\s+([0-9X]+)")


@dataclass(frozen=True)
class CoursePattern:
    subject: str     # 'CSCI-UA'
    number: str      # '102', or '4XX' for a wildcard

    @classmethod
    def parse(cls, text: str) -> CoursePattern:
        """Raises ValueError on anything that is not a code or a wildcard.

        Fails loudly rather than guessing, since a silent wrong reading would
        search the wrong catalog without anyone noticing. A wildcard is only
        ever spelled with x: 'CSCI-UA 4' is course 4, never 'any 4-something',
        because short codes are real (EXPOS-UA 1).
        """
        match = PATTERN_RE.fullmatch(text.strip().upper())
        if not match:
            raise ValueError(f"not a course code or wildcard: {text!r} "
                             f"(expected e.g. 'CSCI-UA 102' or 'CSCI-UA 4xx')")
        subject, number = match.groups()
        if number.startswith("X"):
            # 'xxx' would be the whole subject; ask for it explicitly if wanted.
            raise ValueError(f"a wildcard must start with a digit: {text!r}")
        return cls(subject, number)

    @property
    def is_wildcard(self) -> bool:
        return "X" in self.number

    def matches(self, course_code: str) -> bool:
        """True if a catalog code like 'CSCI-UA 472' falls under this pattern."""
        subject, _, number = course_code.upper().partition(" ")
        if subject != self.subject or len(number) != len(self.number):
            return False
        return all(p == "X" or p == n for p, n in zip(self.number, number))

    def __str__(self) -> str:
        return f"{self.subject} {self.number.replace('X', 'x')}"


def matches_by_pattern(texts: list[str], courses: list) -> dict[str, list]:
    """Each requested text -> the courses it matched, in request order.

    An empty list is a pattern that matched nothing, which callers report as
    missing. More than one is a wildcard, or one code covering several topics.
    """
    return {text: [c for c in courses if CoursePattern.parse(text).matches(c.code)]
            for text in texts}
