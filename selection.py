"""Which courses a schedule contains, before any section is chosen.

Every entry a student gives is a *group*: the courses it matches, how many to
take, and whether taking them is required. An exact code is a group of one;
'CSCI-UA 4xx' and a multi-topic code like CSCI-UA 480 are groups of many. One
concept covers all three, so a wildcard is not a special case anywhere below.

- **Required** takes exactly `count` of its courses.
- **Optional** takes anywhere from 0 to `count` -- the scheduler may leave it
  out, and the credit range is what decides how much of it to take.

This runs *before* scheduling, on whole courses, because it is cheap there:
a combination whose credits fall outside the range is discarded without ever
generating a schedule for it. Section-level work only happens for the sets
that survive.
"""
from __future__ import annotations
import itertools
import re
from dataclasses import dataclass

from models import Course
from patterns import CoursePattern
from scheduler import valid_bundles

# '2x CSCI-UA 4xx' -> count 2. The count is spelled before the code so it can
# never be confused with the code's own x wildcards.
ENTRY_RE = re.compile(r"(?:(\d+)\s*[xX]\s+)?(.+)")

# Raw combinations considered before credit filtering. 2 of 18 electives is
# 153; this guards against something like '3x CSCI-UA 4xx, 3x MATH-UA 3xx'
# silently turning one request into minutes of work.
MAX_COMBINATIONS = 20_000


class SelectionError(ValueError):
    """The request cannot produce any course set; the message says why."""


@dataclass(frozen=True)
class CourseRequest:
    text: str              # 'CSCI-UA 4xx', as typed (normalized)
    required: bool = True
    count: int = 1

    @classmethod
    def parse(cls, entry: str, required: bool = True) -> CourseRequest:
        """'CSCI-UA 102' or '2x CSCI-UA 4xx'. Raises ValueError on a bad code."""
        match = ENTRY_RE.fullmatch(entry.strip())
        count = int(match.group(1)) if match and match.group(1) else 1
        return cls.of(match.group(2) if match else entry, required, count)

    @classmethod
    def of(cls, code: str, required: bool = True, count: int = 1) -> CourseRequest:
        """From already-separated parts, as a structured client sends them."""
        if count < 1:
            raise ValueError(f"count must be at least 1 for {code!r}")
        return cls(str(CoursePattern.parse(code)), required, count)

    @property
    def pattern(self) -> CoursePattern:
        return CoursePattern.parse(self.text)

    def __str__(self) -> str:
        prefix = f"{self.count}x " if self.count > 1 else ""
        return f"{prefix}{self.text}" + ("" if self.required else " (optional)")


@dataclass(frozen=True)
class CreditRange:
    low: float | None = None
    high: float | None = None

    @property
    def is_set(self) -> bool:
        return self.low is not None or self.high is not None

    def allows(self, total: float) -> bool:
        return ((self.low is None or total >= self.low)
                and (self.high is None or total <= self.high))

    def __str__(self) -> str:
        low = "any" if self.low is None else f"{self.low:g}"
        high = "any" if self.high is None else f"{self.high:g}"
        return f"{low}-{high} credits"


@dataclass
class Group:
    request: CourseRequest
    courses: list[Course]          # schedulable candidates, in catalog order


@dataclass
class Selection:
    groups: list[Group]
    sets: list[tuple[Course, ...]]     # each one is scheduled separately
    skipped: list[str]                 # optional candidates that cannot be scheduled


def resolve(requests: list[CourseRequest], courses: list[Course],
            credits: CreditRange = CreditRange()) -> Selection:
    """Turn requests plus the courses the provider found into course sets.

    `courses` is everything matched for the term; a request matching nothing
    is the caller's to report as missing, and simply contributes no group.
    """
    # By object, not code: the seven CSCI-UA 480 topics share a code, and one
    # unschedulable topic must not take the other six down with it.
    stuck = {id(c) for c in courses if not valid_bundles(c)}
    groups: list[Group] = []
    skipped: list[str] = []

    for request in requests:
        matched = [c for c in courses if request.pattern.matches(c.code)]
        if not matched:
            continue
        usable = [c for c in matched if id(c) not in stuck]
        if request.required:
            if len(usable) < request.count:
                raise SelectionError(_too_few(request, matched, usable))
        else:
            # One unschedulable elective (a seminar-only course, say) should
            # not sink a whole wildcard. Named, not hidden.
            skipped += [c.label for c in matched if id(c) in stuck]
        groups.append(Group(request, usable))

    if credits.is_set:
        unknown = sorted({c.label for g in groups for c in g.courses
                          if c.credits is None})
        if unknown:
            # Unknown credits are not zero. Counting them as free would let a
            # 'max 16 credits' quietly admit a 20-credit schedule.
            raise SelectionError(
                f"credits are unknown for {', '.join(unknown)}, so a credit "
                f"range cannot be checked")

    options = [_choices(g) for g in groups]
    size = 1
    for choice in options:
        size *= len(choice)
    if size > MAX_COMBINATIONS:
        raise SelectionError(
            f"{size:,} course combinations is too many to search; take fewer "
            f"from a wildcard, narrow it (e.g. 'CSCI-UA 47x'), or mark fewer "
            f"courses optional")

    sets: list[tuple[Course, ...]] = []
    seen: set[frozenset[int]] = set()
    totals: list[float] = []
    for combo in itertools.product(*options):
        chosen = tuple(c for part in combo for c in part)
        identity = frozenset(id(c) for c in chosen)
        if len(identity) < len(chosen) or identity in seen:
            continue            # a course counted twice, or a set already seen
        seen.add(identity)
        if not chosen:
            continue            # everything optional and nothing taken
        total = sum(c.credits or 0 for c in chosen)
        totals.append(total)
        if credits.allows(total):
            sets.append(chosen)

    if not sets:
        raise SelectionError(_no_sets(credits, totals))
    return Selection(groups, sets, skipped)


def _choices(group: Group) -> list[tuple[Course, ...]]:
    """Every way this group can contribute: exactly `count` if required,
    0..`count` if optional (smallest first, so lighter sets come first)."""
    sizes = ([group.request.count] if group.request.required
             else range(0, group.request.count + 1))
    return [combo for k in sizes
            for combo in itertools.combinations(group.courses, k)]


def _too_few(request: CourseRequest, matched, usable) -> str:
    if len(matched) == 1 and not usable:
        return (f"can't build a registrable set of sections for "
                f"{matched[0].label}, so no schedule can include it")
    note = (f" ({len(matched) - len(usable)} cannot be scheduled)"
            if len(usable) < len(matched) else "")
    return (f"{request.text} needs {request.count} but matches only "
            f"{len(usable)} schedulable course(s){note}")


def _no_sets(credits: CreditRange, totals: list[float]) -> str:
    if not totals:
        return "no course combination is possible from this request"
    return (f"no combination of these courses lands within {credits}; "
            f"possible totals run from {min(totals):g} to {max(totals):g}")
