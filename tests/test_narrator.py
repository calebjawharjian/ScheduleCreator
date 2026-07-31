"""The narration layer.

Everything here is offline. `build_findings` is a pure function precisely so
that the part which *decides* things can be tested without an API key, leaving
the backends as thin transport that only needs its failure handling checked.

The load-bearing property is that findings contain no clock time. That is what
makes the output guard sound: the model is never shown a time, so a time in its
prose was necessarily invented, and inventing the hour a class meets is the one
failure this whole design exists to prevent.
"""
import re

import pytest

from constraints import CommuteBuffer, RequiredInstructor
from models import Day, Meeting, Section, SectionKind, hhmm
from narrator import (
    PROVIDERS, ClaudeNarrator, FallbackNarrator, NarrationUnavailable,
    OpenAICompatNarrator, TemplateNarrator, _ChatNarrator, build_findings,
    narrator_from_env,
)
from scoring import (
    DAYS, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START, PROFESSOR, TIME,
    DistinctSchedule, Preferences, score_schedule,
)

ALL_FACTORS = [DAYS, TIME, PROFESSOR, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START]
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")


def m(day, start, end):
    return Meeting(day, hhmm(start), hhmm(end))


def sec(course, kind=SectionKind.LECTURE, instructor=None, meetings=(),
        campus=None, section_id=None):
    return Section(section_id or f"{course}-{kind.name}", course, kind,
                   instructor, list(meetings), campus=campus)


def prefs(**overrides):
    base = dict(preferred_days={Day.MON, Day.WED},
                time_start=hhmm("12:00"), time_end=hhmm("18:00"),
                professor_ratings={}, weights={n: 1 for n in ALL_FACTORS})
    base.update(overrides)
    return Preferences(**base)


def findings_for(schedule, p=None, variants=1, **kwargs):
    p = p or prefs()
    option = DistinctSchedule(schedule, score_schedule(schedule, p), variants)
    return build_findings(option, p, rank=kwargs.pop("rank", 1), **kwargs)


# --- the invariant that makes the output guard meaningful --------------------

def test_findings_never_contain_a_clock_time():
    """If a time ever leaks in here, the guard in _ChatNarrator silently starts
    rejecting legitimate output -- and worse, a real time could be echoed back
    wrong. Durations in minutes are fine; formatted times are not."""
    schedule = [
        sec("CSCI-UA 102", instructor="Anasse Bari",
            meetings=[m(Day.FRI, "11:00", "13:30")], campus="Washington Square"),
        sec("EG-UY 1004", SectionKind.LAB,
            meetings=[m(Day.FRI, "14:00", "16:50")], campus="Brooklyn (Tandon)"),
    ]
    found = findings_for(schedule, requirements=[
        RequiredInstructor("CSCI-UA 102", "Anasse Bari")])
    assert not CLOCK.search(repr(found)), f"a clock time leaked: {found}"


# --- relational facts: the reason this layer exists at all -------------------

def test_days_name_the_courses_that_force_them():
    """The whole point. 'You are on campus Friday' is templatable; 'Friday is
    Bari's lecture and your Math recitation' is what needs a model."""
    schedule = [
        sec("CSCI-UA 102", instructor="Anasse Bari", meetings=[m(Day.FRI, "11:00", "13:30")]),
        sec("MATH-UA 121", SectionKind.RECITATION, meetings=[m(Day.FRI, "14:00", "15:15")]),
        sec("EXPOS-UA 1", meetings=[m(Day.TUE, "12:30", "13:45")]),
    ]
    days = findings_for(schedule)["days_on_campus"]
    assert days["FRI"] == ["CSCI-UA 102 lecture", "MATH-UA 121 recitation"]
    assert days["TUE"] == ["EXPOS-UA 1 lecture"]


def test_longest_gap_is_bracketed_by_what_causes_it():
    schedule = [
        sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")]),
        sec("B 2", SectionKind.LAB, meetings=[m(Day.MON, "14:00", "15:00")]),
        sec("C 3", meetings=[m(Day.MON, "15:30", "16:00")]),
    ]
    gap = findings_for(schedule)["longest_gap"]
    assert gap == {"day": "MON", "minutes": 240,
                   "after": "A 1 lecture", "before": "B 2 lab"}


def test_no_gap_when_nothing_is_idle():
    schedule = [sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")])]
    assert findings_for(schedule)["longest_gap"] is None


def test_campus_changes_are_reported_with_the_time_between():
    schedule = [
        sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")], campus="Washington Square"),
        sec("B 2", meetings=[m(Day.MON, "10:30", "11:30")], campus="Brooklyn (Tandon)"),
    ]
    hops = findings_for(schedule)["campus_changes"]
    assert hops == [{"day": "MON", "from": "Washington Square",
                     "to": "Brooklyn (Tandon)", "minutes_between": 30}]


def test_same_campus_is_not_a_change():
    schedule = [
        sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")], campus="Washington Square"),
        sec("B 2", meetings=[m(Day.MON, "10:30", "11:30")], campus="Washington Square"),
    ]
    assert findings_for(schedule)["campus_changes"] == []


# --- sacrifices: a property only becomes a trade-off against an alternative --

def test_a_factor_is_only_a_sacrifice_if_something_else_did_better():
    schedule = [sec("A 1", meetings=[m(Day.TUE, "09:00", "10:00")])]
    p = prefs()
    scored = score_schedule(schedule, p)

    # Nothing beat it anywhere: no sacrifices, however low the scores.
    none_better = findings_for(schedule, p, achievable=dict(scored.factors))
    assert none_better["sacrifices"] == []

    # Something reached a perfect preferred-days score, so this gave that up.
    better = dict(scored.factors)
    better[DAYS] = 1.0
    gave_up = findings_for(schedule, p, achievable=better)["sacrifices"]
    assert [s["factor"] for s in gave_up] == ["preferred days"]
    assert gave_up[0]["best_available"] == 1.0


def test_trivial_shortfalls_are_not_reported_as_sacrifices():
    """A 0.85-vs-0.89 gap is not a trade anyone made on purpose. Reporting all
    four shortfalls buried the only one that mattered, and the model dutifully
    listed them all."""
    schedule = [sec("A 1", meetings=[m(Day.MON, "13:00", "14:00")])]
    p = prefs()
    barely = {n: v + 0.02 for n, v in score_schedule(schedule, p).factors.items()}
    assert findings_for(schedule, p, achievable=barely)["sacrifices"] == []


def test_sacrifices_are_ranked_by_size_and_capped():
    schedule = [sec("A 1", meetings=[m(Day.TUE, "09:00", "10:00")])]
    p = prefs()
    scored = score_schedule(schedule, p).factors
    spread = {DAYS: 0.9, TIME: 0.5, PROFESSOR: 0.8,
              DAYS_ON_CAMPUS: 0.3, IDLE_TIME: 0.2, LATE_START: 0.95}
    achievable = {n: min(1.0, scored[n] + spread[n]) for n in scored}

    found = findings_for(schedule, p, achievable=achievable)["sacrifices"]
    assert len(found) == 3, "should keep only the largest few"
    shortfalls = [s["shortfall"] for s in found]
    assert shortfalls == sorted(shortfalls, reverse=True)


def test_day_counts_are_given_as_numbers_not_scores():
    """The model cannot know 'days on campus 0.0' means five days, so the
    count and the best alternative are handed over directly."""
    schedule = [
        sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")]),
        sec("B 2", meetings=[m(Day.WED, "09:00", "10:00")]),
    ]
    found = findings_for(schedule, fewest_days_available=1)
    assert found["days_on_campus_count"] == 2
    assert found["fewest_days_available"] == 1


def test_no_achievable_data_means_no_sacrifices_claimed():
    """Better to say nothing than to assert a trade-off we cannot support."""
    schedule = [sec("A 1", meetings=[m(Day.TUE, "09:00", "10:00")])]
    assert findings_for(schedule)["sacrifices"] == []


# --- requirements and compromises --------------------------------------------

def test_requirements_and_compromises_are_passed_through():
    schedule = [
        sec("CSCI-UA 102", instructor="Anasse Bari",
            meetings=[m(Day.MON, "09:00", "10:00")], campus="Washington Square"),
        sec("EG-UY 1004", meetings=[m(Day.MON, "10:10", "11:00")],
            campus="Brooklyn (Tandon)"),
    ]
    buffer = CommuteBuffer(30)
    found = findings_for(
        schedule,
        requirements=[RequiredInstructor("CSCI-UA 102", "Anasse Bari")],
        compromised=[buffer])

    assert found["required"][0]["requirement"] == "CSCI-UA 102 taught by Anasse Bari"
    # The day the requirement actually pins, not just that one exists.
    assert found["required"][0]["meets_on"] == ["MON"]
    assert found["compromises"][0]["requirement"] == "30 minutes between campuses"
    assert found["compromises"][0]["details"], "should name the failing transition"


def test_unrated_professors_are_named_separately():
    """So the narrator can say a rating was unavailable rather than imply the
    professor is bad -- the same trap that made avgRating 0 dangerous."""
    schedule = [sec("X 1", instructor="Nobody Known",
                    meetings=[m(Day.MON, "12:00", "13:00")])]
    found = findings_for(schedule, prefs(professor_ratings={}))
    assert found["unrated_professors"] == ["Nobody Known"]
    assert found["professors"][0]["rating"] is None


def test_variants_are_reported_so_scarcity_can_be_described():
    schedule = [sec("X 1", meetings=[m(Day.MON, "12:00", "13:00")])]
    assert findings_for(schedule, variants=27)["interchangeable_variants"] == 27


# --- the deterministic fallback ----------------------------------------------

def test_template_narrator_needs_no_network_or_key():
    schedule = [sec("X 1", meetings=[m(Day.MON, "12:00", "13:00")])]
    text = TemplateNarrator().narrate(findings_for(schedule))
    assert "strongest:" in text and "biggest tradeoff:" in text


def test_template_reproduces_the_pre_narration_output_exactly():
    """Routing this output through the narrator seam must not quietly change
    it. The numbers were dropped once already when findings carried only the
    factor labels."""
    schedule = [sec("X 1", meetings=[m(Day.MON, "12:00", "13:00")])]
    text = TemplateNarrator().narrate(findings_for(schedule))
    assert re.fullmatch(
        r"strongest: .+ at \d\.\d\d\n"
        r"biggest tradeoff: .+ costs \d\.\d\d of the total", text), text


def test_template_narrator_reports_compromises():
    schedule = [
        sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")], campus="Washington Square"),
        sec("B 2", meetings=[m(Day.MON, "10:10", "11:00")], campus="Brooklyn (Tandon)"),
    ]
    found = findings_for(schedule, compromised=[CommuteBuffer(30)])
    assert "could not be met" in TemplateNarrator().narrate(found)


# --- output validation --------------------------------------------------------

class _Fixed(_ChatNarrator):
    def __init__(self, reply):
        self.reply = reply

    def _complete(self, system, user):
        return self.reply


def test_an_invented_clock_time_is_refused():
    """It was never given a time, so this one is fabricated. Printing it would
    put a wrong meeting time in front of someone about to register."""
    with pytest.raises(NarrationUnavailable, match="invented a time"):
        _Fixed("Your Monday starts at 9:30 and it is rough.").narrate({})


def test_an_empty_response_is_refused():
    with pytest.raises(NarrationUnavailable):
        _Fixed("   ").narrate({})


def test_clean_prose_passes_through():
    text = "Bari only teaches Friday, so a compact week was never available."
    assert _Fixed(text).narrate({}) == text


def test_durations_in_words_are_not_mistaken_for_times():
    text = "You wait an hour and forty minutes between the lecture and its lab."
    assert _Fixed(text).narrate({}) == text


# --- degrading instead of crashing --------------------------------------------

class _Broken(_ChatNarrator):
    def _complete(self, system, user):
        raise NarrationUnavailable("no key")


def test_failure_falls_back_to_the_deterministic_text():
    schedule = [sec("X 1", meetings=[m(Day.MON, "12:00", "13:00")])]
    found = findings_for(schedule)
    assert FallbackNarrator(_Broken()).narrate(found) == TemplateNarrator().narrate(found)


def test_the_failure_warning_fires_once_not_per_schedule():
    """Ten schedules with no API key should not print ten identical warnings."""
    seen = []
    narrator = FallbackNarrator(_Broken(), on_error=seen.append)
    schedule = [sec("X 1", meetings=[m(Day.MON, "12:00", "13:00")])]
    for _ in range(5):
        narrator.narrate(findings_for(schedule))
    assert len(seen) == 1
    assert narrator.failures == 5


# --- backend selection --------------------------------------------------------

def test_no_credentials_gives_the_template_not_an_error():
    narrator, name = narrator_from_env({})
    assert name == "template"
    assert isinstance(narrator, TemplateNarrator)


def test_free_providers_are_preferred_over_paid():
    """Stated goal: run free where possible. Anthropic always bills, so it must
    never win over a working free key."""
    _, name = narrator_from_env({"ANTHROPIC_API_KEY": "k", "GROQ_API_KEY": "g"})
    assert name == "groq"


def test_anthropic_is_used_when_it_is_the_only_key():
    narrator, name = narrator_from_env({"ANTHROPIC_API_KEY": "k"})
    assert name == "anthropic"
    assert isinstance(narrator, ClaudeNarrator)


def test_an_explicit_provider_overrides_the_order():
    narrator, name = narrator_from_env(
        {"GROQ_API_KEY": "g", "DEEPSEEK_API_KEY": "d",
         "NARRATOR_PROVIDER": "deepseek"})
    assert name == "deepseek"
    assert isinstance(narrator, OpenAICompatNarrator)
    assert narrator.base_url == PROVIDERS["deepseek"][0]


def test_ollama_needs_no_key_but_must_be_asked_for():
    """It cannot be auto-selected: a missing key cannot distinguish a running
    local server from no server at all."""
    _, auto = narrator_from_env({})
    assert auto == "template"
    narrator, name = narrator_from_env({"NARRATOR_PROVIDER": "ollama"})
    assert name == "ollama" and narrator.api_key is None


def test_an_explicit_provider_without_credentials_fails_loudly():
    """Asking for a provider and silently getting a different one would hide a
    typo'd env var behind plausible-looking output."""
    with pytest.raises(SystemExit, match="no usable credentials"):
        narrator_from_env({"NARRATOR_PROVIDER": "groq"})


def test_an_unknown_provider_name_fails_loudly():
    with pytest.raises(SystemExit, match="Known providers"):
        narrator_from_env({"NARRATOR_PROVIDER": "gpt5"})


def test_model_can_be_overridden_without_touching_code():
    narrator, _ = narrator_from_env(
        {"GROQ_API_KEY": "g", "NARRATOR_MODEL": "llama-3.1-8b-instant"})
    assert narrator.model == "llama-3.1-8b-instant"


def test_a_requirement_reports_only_the_days_its_own_section_meets():
    """Regression on a wrong claim in real output: with Bari teaching Friday
    and the recitation on Monday, the model wrote 'CSCI-UA 102 on Monday
    because it is required'. The requirement pins Friday only."""
    schedule = [
        sec("CSCI-UA 102", instructor="Anasse Bari",
            meetings=[m(Day.FRI, "11:00", "13:30")]),
        sec("CSCI-UA 102", SectionKind.RECITATION,
            meetings=[m(Day.MON, "09:30", "10:45")], section_id="rct"),
    ]
    found = findings_for(schedule, requirements=[
        RequiredInstructor("CSCI-UA 102", "Anasse Bari")])
    assert found["required"][0]["meets_on"] == ["FRI"]


def test_a_requirement_with_no_matching_section_claims_no_days():
    """CommuteBuffer and the like pin no particular day, so they must not
    acquire one."""
    schedule = [
        sec("A 1", meetings=[m(Day.MON, "09:00", "10:00")], campus="Washington Square"),
        sec("B 2", meetings=[m(Day.MON, "10:10", "11:00")], campus="Brooklyn (Tandon)"),
    ]
    found = findings_for(schedule, requirements=[CommuteBuffer(30)])
    assert "meets_on" not in found["required"][0]
