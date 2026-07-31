"""Plain-language explanation of a ranked schedule.

The point of this layer is *why*, not *what*. `main.py` already prints every
fact a student registers against -- CRNs, days, times -- deterministically, and
that must never depend on a model phrasing it correctly. What no amount of
string formatting produces is the connective tissue: that Friday exists only
because the required professor teaches then, that a compact week was never on
offer, that the Brooklyn trip came up short because nothing else fit.

So the split is: **code decides, the model phrases.** `build_findings` does all
the deciding and returns a dict where every number is already settled. The
model receives that dict and writes prose. It never scores, ranks, or judges,
and it is never given raw meeting times -- it cannot misstate a time it was
never shown, and any clock time appearing in its output is therefore known to
be invented (see `_INVENTED_TIME`).

Narration is presentational, so failure degrades instead of raising: no key, no
network, or a bad response falls back to `TemplateNarrator`, which is exactly
the deterministic output this project shipped before narration existed. That is
a deliberate exception to "fail loudly on unknown input", which governs *data*
correctness; losing commentary is not the same as getting a fact wrong.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import json
import os
import re
import textwrap

from models import Day, Section, SectionKind
from scoring import (DistinctSchedule, FACTOR_LABELS, IdealComparison,
                     Preferences, compare_to_ideal)

# Every provider below speaks the OpenAI chat-completions shape, so one client
# covers all of them. Model names drift; override with NARRATOR_MODEL.
PROVIDERS: dict[str, tuple[str, str, str | None]] = {
    # name:        (base_url, default model, key env var)
    "groq":        ("https://api.groq.com/openai/v1",
                    "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "gemini":      ("https://generativelanguage.googleapis.com/v1beta/openai",
                    "gemini-2.0-flash", "GEMINI_API_KEY"),
    "openrouter":  ("https://openrouter.ai/api/v1",
                    "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
    "deepseek":    ("https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY"),
    # Local, no key. Never auto-selected (we cannot cheaply prove it is
    # running), so it needs NARRATOR_PROVIDER=ollama.
    "ollama":      ("http://localhost:11434/v1", "llama3.2", None),
}

# Preference order when several keys are present. Free tiers first, which is
# the stated goal; Anthropic is last because it always bills.
AUTO_ORDER = ("groq", "gemini", "openrouter", "deepseek", "anthropic")

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = textwrap.dedent("""\
    You explain university course schedules to the student who will register
    for one. You are given a JSON object of facts that have already been
    decided by a deterministic scheduler. Your only job is to phrase them.

    Write 2-3 sentences, second person, plain and specific. Explain WHY this
    schedule looks the way it does and what it gave up -- the causes and the
    sacrifices, not a list of its properties. Lead with whatever actually
    constrained the outcome (a required professor, a campus commute, a course
    with few sections).

    Hard rules:
    - Use only facts present in the JSON. Never invent or infer a number.
    - Never write a clock time. You are not given any. Express durations in
      words: "an hour and forty minutes", never "1:40".
    - Do not restate the numeric score, and do not recommend or rank. The
      student is shown the exact days, times and CRNs separately, so do not
      attempt to list them.
    - If `sacrifices` is empty, say plainly that nothing had to be given up.
    """)

# We never supply clock times, so one in the output was fabricated.
_INVENTED_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")


class NarrationUnavailable(Exception):
    """Raised inside a backend; callers fall back rather than crash."""


# --- the facts ---------------------------------------------------------------

def _by_day(schedule: list[Section]) -> dict[Day, list[tuple]]:
    """(meeting, section) per day, earliest first."""
    days: dict[Day, list[tuple]] = {}
    for section in schedule:
        for meeting in section.meetings:
            days.setdefault(meeting.day, []).append((meeting, section))
    for day in days:
        days[day].sort(key=lambda pair: pair[0].start)
    return days


def _label(section: Section) -> str:
    return f"{section.course_code} {section.kind.name.lower()}"


def _longest_gap(days: dict[Day, list[tuple]]) -> dict | None:
    """The worst idle stretch, named by what brackets it.

    Bracketing is the whole point: "a two-hour hole on Monday" is a property,
    but "two hours between your Tandon lecture and its recitation" is a cause.
    """
    worst = None
    for day, timeline in days.items():
        for (earlier, before), (later, after) in zip(timeline, timeline[1:]):
            gap = later.start - earlier.end
            if gap > 0 and (worst is None or gap > worst["minutes"]):
                worst = {"day": day.name, "minutes": gap,
                         "after": _label(before), "before": _label(after)}
    return worst


def _campus_hops(days: dict[Day, list[tuple]]) -> list[dict]:
    hops = []
    for day, timeline in days.items():
        for (earlier, before), (later, after) in zip(timeline, timeline[1:]):
            a, b = before.campus, after.campus
            if a and b and a != b:
                hops.append({"day": day.name, "from": a, "to": b,
                             "minutes_between": later.start - earlier.end})
    return hops


def build_findings(
    option: DistinctSchedule,
    prefs: Preferences,
    *,
    rank: int,
    achievable: dict[str, float] | None = None,
    requirements=(),
    compromised=(),
) -> dict:
    """Everything the narrator is allowed to know, all of it already decided.

    Deliberately excludes raw meeting times. The student sees those printed
    exactly; handing them to a model only creates a way for them to come back
    wrong. Durations survive because a gap's *length* is the interesting part
    and cannot be confused for a time to show up somewhere.

    `achievable` is the best score any surviving schedule reached on each
    factor. It is what turns a property into a sacrifice: five days on campus
    means nothing on its own, but five days when some other schedule managed
    three is a trade the student actually made.
    """
    schedule = option.schedule
    comparison: IdealComparison = compare_to_ideal(option.breakdown, prefs)
    days = _by_day(schedule)

    lectures = [s for s in schedule if s.kind == SectionKind.LECTURE]
    def rating_of(name: str) -> float | None:
        # Rounded to what the display shows. Handing over 4.268379446640315
        # invites the model to quote a precision the rating does not have.
        value = prefs.professor_ratings.get(name)
        return None if value is None else round(value, 1)

    professors = [
        {"course": s.course_code, "name": s.instructor,
         "rating": rating_of(s.instructor)}
        for s in lectures if s.instructor
    ]

    outside_window = sum(
        1 for s in schedule for m in s.meetings
        if m.start < prefs.time_start or m.end > prefs.time_end)

    sacrifices = []
    for name, score in option.breakdown.factors.items():
        best = (achievable or {}).get(name)
        if best is not None and best - score > 0.01:
            sacrifices.append({
                "factor": FACTOR_LABELS[name],
                "this_schedule": round(score, 2),
                "best_available": round(best, 2),
            })

    return {
        "rank": rank,
        "interchangeable_variants": option.variants,
        # Which courses force each day, so "why am I here Friday" is answerable.
        "days_on_campus": {
            day.name: sorted({_label(s) for _, s in timeline})
            for day, timeline in sorted(days.items())
        },
        "days_outside_preference": sorted(
            day.name for day in days if day not in prefs.preferred_days),
        # A count, not the window itself. Findings deliberately contain no
        # clock time anywhere, which is what makes the output guard sound: any
        # clock time in the prose was necessarily invented.
        "meetings_outside_time_window": outside_window,
        "longest_gap": _longest_gap(days),
        "campus_changes": _campus_hops(days),
        "professors": professors,
        "unrated_professors": [s.instructor for s in lectures
                               if s.instructor and s.instructor
                               not in prefs.professor_ratings],
        "required": [r.describe() for r in requirements],
        "compromises": [
            {"requirement": c.describe(),
             "details": c.violations(schedule) if hasattr(c, "violations") else []}
            for c in compromised
        ],
        "factor_scores": {FACTOR_LABELS[n]: round(v, 2)
                          for n, v in option.breakdown.factors.items()},
        "sacrifices": sacrifices,
        "strongest": FACTOR_LABELS[comparison.strengths[0].name],
        "biggest_tradeoff": FACTOR_LABELS[comparison.weaknesses[0].name],
        # Carried so TemplateNarrator can reproduce the exact pre-narration
        # output. The model is told not to restate scores; the fallback must.
        "strongest_score": round(comparison.strengths[0].score, 2),
        "biggest_tradeoff_cost": round(comparison.weaknesses[0].weighted_gap, 2),
    }


# --- the narrators -----------------------------------------------------------

class Narrator(ABC):
    """Turns a decided findings dict into prose."""

    @abstractmethod
    def narrate(self, findings: dict) -> str:
        ...


class TemplateNarrator(Narrator):
    """Deterministic fallback: the output this project had before narration.

    Not a stub. It is what runs with no key, no network, or a bad response, so
    a run never depends on a model being reachable.
    """

    def narrate(self, findings: dict) -> str:
        lines = [
            f"strongest: {findings['strongest']} "
            f"at {findings['strongest_score']:.2f}",
            f"biggest tradeoff: {findings['biggest_tradeoff']} "
            f"costs {findings['biggest_tradeoff_cost']:.2f} of the total",
        ]
        for compromise in findings.get("compromises", []):
            lines.append(f"compromise: {compromise['requirement']} could not be met")
        return "\n".join(lines)


class _ChatNarrator(Narrator):
    """Shared prompt handling and output validation for the API backends."""

    def _complete(self, system: str, user: str) -> str:
        raise NotImplementedError

    def narrate(self, findings: dict) -> str:
        text = self._complete(SYSTEM_PROMPT, json.dumps(findings, indent=2)).strip()
        if not text:
            raise NarrationUnavailable("empty response")
        if _INVENTED_TIME.search(text):
            # It was never given a clock time, so it made this one up. A
            # fabricated time is exactly the failure the fact/prose split
            # exists to prevent, so refuse it rather than print it.
            raise NarrationUnavailable(f"invented a time: {text[:80]!r}")
        return text


class OpenAICompatNarrator(_ChatNarrator):
    """Any provider speaking OpenAI's chat-completions shape.

    Covers Groq, Gemini's compatibility endpoint, OpenRouter, DeepSeek and a
    local Ollama. Written on `requests`, already a dependency, rather than the
    `openai` package -- the call is one POST and this keeps the free options
    dependency-free.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None,
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _complete(self, system: str, user: str) -> str:
        import requests

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={"model": self.model, "temperature": 0, "max_tokens": 300,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=self.timeout)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise NarrationUnavailable(f"{self.model}: {exc}") from exc


class ClaudeNarrator(_ChatNarrator):
    def __init__(self, api_key: str, model: str = ANTHROPIC_MODEL):
        self.api_key = api_key
        self.model = model

    def _complete(self, system: str, user: str) -> str:
        try:
            import anthropic

            message = anthropic.Anthropic(api_key=self.api_key).messages.create(
                model=self.model, max_tokens=300, temperature=0, system=system,
                messages=[{"role": "user", "content": user}])
            return "".join(block.text for block in message.content
                           if block.type == "text")
        except Exception as exc:
            raise NarrationUnavailable(f"{self.model}: {exc}") from exc


class FallbackNarrator(Narrator):
    """Try the model; on any failure use the deterministic text instead.

    Wrapping rather than try/except at the call site keeps `main.py` unaware
    that narration can fail at all.
    """

    def __init__(self, primary: Narrator, fallback: Narrator | None = None,
                 on_error=None):
        self.primary = primary
        self.fallback = fallback or TemplateNarrator()
        self.on_error = on_error
        self.failures = 0

    def narrate(self, findings: dict) -> str:
        try:
            return self.primary.narrate(findings)
        except NarrationUnavailable as exc:
            self.failures += 1
            if self.on_error and self.failures == 1:   # warn once, not per schedule
                self.on_error(exc)
            return self.fallback.narrate(findings)


def load_env(path: str = ".env") -> None:
    """Read .env if python-dotenv is installed. Never overrides a real export."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(path, override=False)


def narrator_from_env(env: dict | None = None) -> tuple[Narrator, str]:
    """Pick a backend from whatever credentials exist. Returns (narrator, name).

    Free providers are tried first, then paid, then the template. Selection is
    explicit via NARRATOR_PROVIDER, which is also the only way to reach a local
    Ollama, since a missing key cannot distinguish "running" from "absent".
    """
    env = os.environ if env is None else env
    override = env.get("NARRATOR_PROVIDER", "").strip().lower()
    model_override = env.get("NARRATOR_MODEL", "").strip() or None

    def build(name: str) -> Narrator | None:
        if name == "anthropic":
            key = env.get("ANTHROPIC_API_KEY")
            return ClaudeNarrator(key, model_override or ANTHROPIC_MODEL) if key else None
        preset = PROVIDERS.get(name)
        if preset is None:
            return None
        base_url, model, key_env = preset
        key = env.get(key_env) if key_env else None
        if key_env and not key:
            return None
        return OpenAICompatNarrator(base_url, model_override or model, key)

    if override:
        chosen = build(override)
        if chosen is None:
            raise SystemExit(
                f"NARRATOR_PROVIDER={override!r} but no usable credentials. "
                f"Known providers: {', '.join(sorted({*PROVIDERS, 'anthropic'}))}")
        return chosen, override

    for name in AUTO_ORDER:
        chosen = build(name)
        if chosen is not None:
            return chosen, name
    return TemplateNarrator(), "template"
