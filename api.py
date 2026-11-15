"""HTTP layer over the scheduler. Contains no scheduling logic of its own.

Split into two endpoints because the costs are wildly asymmetric, and that
asymmetry is the whole reason the UI can feel live. Measured end to end over
HTTP on a real four-course list (120,119 schedules):

    POST /generate  cold    18.7s   term + courses; also the network fetches
    POST /generate  cached   0.00s
    POST /rank      cold     0.73s  required professors + commute buffer
    POST /rank      warm     0.14s  preferred days, time window, weights

The user drags weight sliders continuously and changes constraints
deliberately, so `/generate` runs once per course list, the filtered set is
cached separately per constraint set, and a slider drag costs only the warm
ranking pass. One endpoint doing all three would make every drag a
~19-second request.

Caches are in-process dicts. That is honest for a single-process personal
project and deliberately the thing to replace first if this is ever deployed
across workers -- see `_Store`.
"""
from __future__ import annotations
from dataclasses import dataclass
import os

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from campuses import campuses_in
from constraints import CommuteBuffer, RequiredInstructor, best_effort
from models import Day, Section, clock, hhmm
from narrator import build_findings, load_env, narrator_from_env, TemplateNarrator
from scheduler import generate_for_sets
from selection import CourseRequest, CreditRange, Selection, SelectionError, resolve
from scoring import (
    DAYS, TIME, PROFESSOR, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START, CREDIT_LOAD,
    DEFAULT_CREDIT_TARGET, FACTOR_LABELS, schedule_credits,
    Preferences, compare_to_ideal, distinct_schedules, diverse_top, rank_schedules,
)

load_env()

# The one list of offered terms: request validation and /factors both read it.
# Fall 2026 (1268) was dropped once NYU stopped serving it.
TERM_CODES = {"spring2026": "1264", "spring2027": "1274"}
DEFAULT_TERM = "spring2027"
DEFAULT_WEIGHTS = {PROFESSOR: 3, TIME: 2, DAYS: 1,
                   DAYS_ON_CAMPUS: 1, IDLE_TIME: 1, LATE_START: 1, CREDIT_LOAD: 1}

app = FastAPI(title="ScheduleCreator", version="0.1")

# The dev frontend runs on a different port, so the browser treats it as a
# different origin and blocks every call unless the origin is allow-listed.
# Overridable so `dev.sh` can keep both sides consistent when the ports are
# changed; a deployed instance must set this to its real origin rather than
# relying on the localhost default.
ALLOWED_ORIGINS = [
    origin.strip() for origin in
    os.environ.get("SCHEDULER_ALLOWED_ORIGINS",
                   "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- wire format -------------------------------------------------------------

class MeetingOut(BaseModel):
    day: int                     # 0 = Monday, matching models.Day
    day_name: str
    start: int                   # minutes since midnight, for grid layout
    end: int
    start_label: str             # pre-formatted, so the UI never re-derives a time
    end_label: str


class SectionOut(BaseModel):
    section_id: str              # the CRN the student registers with
    course_code: str
    kind: str
    section_no: str | None
    instructor: str | None
    rating: float | None
    campus: str | None
    meetings: list[MeetingOut]


class FactorOut(BaseModel):
    name: str
    label: str
    score: float
    weight_share: float
    weighted_gap: float


class AlternativeOut(BaseModel):
    rank: int
    reason: str                  # "best overall", "best for days on campus", ...
    factor: str | None
    score: float
    gap_from_ideal: float
    variants: int                # other section combinations at these same times
    days_on_campus: int
    credits: float | None        # this schedule's total; None if any are unknown
    sections: list[SectionOut]
    factors: list[FactorOut]
    explanation: str | None = None


class CourseEntry(BaseModel):
    """One requested group: an exact code, a wildcard ('CSCI-UA 4xx'), or a
    multi-topic code. Required takes exactly `count`; optional takes 0..count."""
    code: str
    required: bool = True
    count: int = Field(default=1, ge=1)


class GenerateRequest(BaseModel):
    term: str = DEFAULT_TERM
    # A plain string is a required entry ('2x CSCI-UA 4xx' sets a count), so
    # clients that only send codes keep working unchanged.
    courses: list[str | CourseEntry] = Field(min_length=1)
    credits_min: float | None = None
    credits_max: float | None = None

    @field_validator("term")
    @classmethod
    def _known_term(cls, term: str) -> str:
        # Checked against TERM_CODES rather than a Literal, so adding a term is
        # one edit instead of two lists that must be kept in agreement.
        if term not in TERM_CODES:
            raise ValueError(f"unknown term {term!r}; choose from {sorted(TERM_CODES)}")
        return term


class CourseOut(BaseModel):
    code: str
    title: str
    topic: str | None            # set when one code is several courses (CSCI-UA 480)
    credits: float | None        # None = unknown, never "zero credits"
    sections: int
    instructors: list[str]


class GroupOut(BaseModel):
    code: str                    # as requested: 'CSCI-UA 4xx'
    required: bool
    count: int
    matches: list[str]           # schedulable course labels it can draw from


class GenerateResponse(BaseModel):
    generation_id: str
    courses: list[CourseOut]     # every schedulable candidate, across all groups
    groups: list[GroupOut]
    missing: list[str]           # requested but not found, surfaced not swallowed
    skipped: list[str]           # optional candidates that cannot be scheduled
    course_sets: int             # course combinations scheduled
    campuses: list[str]
    total_schedules: int
    ratings: dict[str, float]


class RankRequest(BaseModel):
    generation_id: str
    preferred_days: list[str] = ["MON", "WED"]
    time_start: str = "12:00"
    time_end: str = "18:00"
    weights: dict[str, float] = {}
    # A ranking preference, not a generation input: changing it re-ranks in a
    # fraction of a second instead of re-running generation.
    credit_target: float = Field(default=DEFAULT_CREDIT_TARGET, ge=0)
    required: dict[str, str] = {}        # course code -> instructor
    commute_minutes: int = 0
    limit: int = Field(default=10, ge=1, le=50)
    narrate: int = Field(default=0, ge=0, le=10)


class RankResponse(BaseModel):
    eligible: int
    distinct_weeks: int
    compromises: list[str]       # constraints that had to bend, never hidden
    alternatives: list[AlternativeOut]


# --- state -------------------------------------------------------------------

@dataclass
class Generation:
    term: str
    courses: list                # schedulable candidates across every group
    selection: Selection         # the groups and the course sets scheduled
    schedules: list[list[Section]]
    ratings: dict[str, float]


class _Store:
    """In-process memo for the two expensive stages.

    Unbounded and per-process on purpose: it is the simplest thing that makes
    the latency budget work for one user on one machine. Anything multi-user
    needs this replaced with a shared cache and an eviction policy, and that
    is a deployment decision rather than a scheduling one.
    """

    def __init__(self):
        self.generations: dict[str, Generation] = {}
        self.filtered: dict[tuple, tuple[list, list]] = {}


store = _Store()


# --- helpers -----------------------------------------------------------------

def _meeting_out(meeting) -> MeetingOut:
    return MeetingOut(
        day=int(meeting.day), day_name=meeting.day.name,
        start=meeting.start, end=meeting.end,
        start_label=clock(meeting.start), end_label=clock(meeting.end))


def _section_out(section: Section, ratings: dict[str, float]) -> SectionOut:
    return SectionOut(
        section_id=section.section_id, course_code=section.course_code,
        kind=section.kind.name, section_no=section.section_no,
        instructor=section.instructor,
        rating=ratings.get(section.instructor) if section.instructor else None,
        campus=section.campus,
        meetings=[_meeting_out(m) for m in
                  sorted(section.meetings, key=lambda m: (m.day, m.start))])


def _generation_key(term: str, requests_: list[CourseRequest],
                    credits: CreditRange) -> str:
    """Everything that changes which schedules exist. Order-insensitive, so
    re-listing the same courses never misses the cache."""
    parts = sorted(str(r) for r in requests_)
    if credits.is_set:
        parts.append(str(credits))
    return f"{term}:" + ",".join(parts)


def _require(generation_id: str) -> Generation:
    generation = store.generations.get(generation_id)
    if generation is None:
        # Unknown ids fail loudly: silently regenerating would turn a client
        # bug into a mysterious 13-second request.
        raise HTTPException(404, "unknown generation_id; call /generate first")
    return generation


# --- endpoints ---------------------------------------------------------------

@app.post("/generate", response_model=GenerateResponse)
def generate(request: GenerateRequest) -> GenerateResponse:
    """Fetch, bundle and enumerate. Expensive, and cached by term + courses."""
    from combined_instructors import CombinedInstructors
    from cs_instructors import CSInstructors
    from ma_instructors import MAInstructors
    from nyu_provider import NYUProvider
    from ratings import RMPRatings, InstructorHint, department_hint

    term = TERM_CODES[request.term]
    try:
        requests_ = [CourseRequest.parse(e) if isinstance(e, str)
                     else CourseRequest.of(e.code, e.required, e.count)
                     for e in request.courses]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    credits = CreditRange(request.credits_min, request.credits_max)
    key = _generation_key(request.term, requests_, credits)

    if key not in store.generations:
        provider = NYUProvider(
            instructors=CombinedInstructors(MAInstructors(), CSInstructors()))
        try:
            courses = provider.get_courses(term, [r.text for r in requests_])
        except requests.RequestException as exc:
            # Raised, not left to crash: an unhandled exception is answered by
            # Starlette's outermost middleware, *outside* CORS, so the 500 has
            # no allow-origin header and the browser reports only "Failed to
            # fetch". That hid this failure on the deployed instance.
            raise HTTPException(
                502, f"NYU's class search request failed: {exc}") from exc
        if not courses:
            raise HTTPException(404, "no sections found for any requested course")
        try:
            selection = resolve(requests_, courses, credits)
        except SelectionError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Only courses some set can actually contain: candidates dropped as
        # unschedulable are reported in `skipped`, not offered.
        courses = [c for c in courses
                   if any(c in g.courses for g in selection.groups)]

        wanted: dict[str, InstructorHint] = {}
        for course in courses:
            for section in course.sections:
                if section.instructor:
                    wanted.setdefault(section.instructor, InstructorHint(
                        department=department_hint(section.course_code),
                        campus=section.campus))
        ratings = RMPRatings(verbose=False).ratings_for(wanted) if wanted else {}

        store.generations[key] = Generation(
            term=term, courses=courses, selection=selection,
            schedules=generate_for_sets(selection.sets), ratings=ratings)

    generation = store.generations[key]
    found = {g.request.text for g in generation.selection.groups}

    return GenerateResponse(
        generation_id=key,
        courses=[CourseOut(
            code=c.code, title=c.title, topic=c.topic, credits=c.credits,
            sections=len(c.sections),
            instructors=sorted({s.instructor for s in c.sections if s.instructor}),
        ) for c in generation.courses],
        groups=[GroupOut(code=g.request.text, required=g.request.required,
                         count=g.request.count,
                         matches=[c.label for c in g.courses])
                for g in generation.selection.groups],
        missing=[r.text for r in requests_ if r.text not in found],
        skipped=generation.selection.skipped,
        course_sets=len(generation.selection.sets),
        campuses=sorted(campuses_in(generation.courses)),
        total_schedules=len(generation.schedules),
        ratings=generation.ratings,
    )


@app.post("/rank", response_model=RankResponse)
def rank(request: RankRequest) -> RankResponse:
    """Filter, score, collapse and diversify. Cheap, and the interactive path."""
    generation = _require(request.generation_id)

    unknown = [name for name in request.weights if name not in FACTOR_LABELS]
    if unknown:
        raise HTTPException(422, f"unknown factor(s): {sorted(unknown)}")
    bad_days = [d for d in request.preferred_days if d not in Day.__members__]
    if bad_days:
        raise HTTPException(422, f"unknown day(s): {bad_days}")

    loaded = {c.code for c in generation.courses}
    stray = [code for code in request.required if code not in loaded]
    if stray:
        # A requirement naming a course that was never requested is vacuous,
        # and vacuously ignoring a stated non-negotiable is the bug this
        # check exists to prevent.
        raise HTTPException(422, f"required course(s) not in this generation: {stray}")

    # Constraints change deliberately (a click), preferences continuously (a
    # drag), so the filtered set is memoized separately from the ranking.
    constraint_key = (request.generation_id,
                      tuple(sorted(request.required.items())),
                      request.commute_minutes)
    if constraint_key not in store.filtered:
        required = [RequiredInstructor(code, name)
                    for code, name in sorted(request.required.items())]
        preferred = ([CommuteBuffer(request.commute_minutes)]
                     if request.commute_minutes > 0 else [])
        store.filtered[constraint_key] = best_effort(
            generation.schedules, required, preferred)
    eligible, compromised = store.filtered[constraint_key]

    if not eligible:
        return RankResponse(eligible=0, distinct_weeks=0,
                            compromises=[c.describe() for c in compromised],
                            alternatives=[])

    prefs = Preferences(
        preferred_days={Day[d] for d in request.preferred_days},
        time_start=hhmm(request.time_start),
        time_end=hhmm(request.time_end),
        professor_ratings=generation.ratings,
        weights={**DEFAULT_WEIGHTS, **request.weights},
        credit_target=request.credit_target,
    )

    ranked = rank_schedules(eligible, prefs)
    weeks = distinct_schedules(ranked)
    picks = diverse_top(weeks, limit=request.limit)

    achievable = {name: max(b.factors[name] for _, b in ranked)
                  for name in FACTOR_LABELS}
    fewest_days = min(len({m.day for s in sched for m in s.meetings})
                      for sched, _ in ranked)

    narrator = TemplateNarrator()
    if request.narrate:
        chosen, _name = narrator_from_env()
        narrator = chosen

    out: list[AlternativeOut] = []
    for index, alternative in enumerate(picks, 1):
        option = alternative.option
        comparison = compare_to_ideal(option.breakdown, prefs)
        days = {m.day for s in option.schedule for m in s.meetings}

        explanation = None
        if index <= request.narrate:
            findings = build_findings(
                option, prefs, rank=index, achievable=achievable,
                fewest_days_available=fewest_days,
                requirements=[RequiredInstructor(c, n)
                              for c, n in sorted(request.required.items())],
                compromised=compromised)
            try:
                explanation = narrator.narrate(findings)
            except Exception:
                explanation = None   # narration is decoration; never fail a rank

        out.append(AlternativeOut(
            rank=index, reason=alternative.reason, factor=alternative.factor,
            score=round(comparison.total, 4),
            gap_from_ideal=round(comparison.total_gap, 4),
            variants=option.variants,
            days_on_campus=len(days),
            credits=schedule_credits(option.schedule),
            sections=[_section_out(s, generation.ratings)
                      for s in sorted(option.schedule,
                                      key=lambda s: (s.course_code, s.kind))],
            factors=[FactorOut(name=g.name, label=FACTOR_LABELS[g.name],
                               score=round(g.score, 4),
                               weight_share=round(g.weight_share, 4),
                               weighted_gap=round(g.weighted_gap, 4))
                     for g in comparison.factors],
            explanation=explanation,
        ))

    return RankResponse(
        eligible=len(eligible), distinct_weeks=len(weeks),
        compromises=[c.describe() for c in compromised], alternatives=out)


@app.get("/factors")
def factors() -> dict:
    """What the weight sliders should render, so the UI never hardcodes them."""
    return {"factors": [{"name": n, "label": l, "default": DEFAULT_WEIGHTS.get(n, 1)}
                        for n, l in FACTOR_LABELS.items()],
            "terms": sorted(TERM_CODES),
            "default_term": DEFAULT_TERM,
            "default_credit_target": DEFAULT_CREDIT_TARGET,
            "days": [d.name for d in Day]}
