"""The HTTP layer.

Offline like everything else: the provider is served from recorded fixtures
and ratings are stubbed out, so these exercise real routing, validation,
serialization and caching without touching NYU or RMP.

The behaviour worth protecting here is not "does it return 200" but the reason
the split exists at all -- that `/rank` reuses `/generate`'s work, and reuses
the filtered set across preference changes. If that regresses the API still
answers correctly, just far too slowly to drag a slider against.
"""
import pytest
from fastapi.testclient import TestClient

import api
from conftest import FALL_2026, load_brooklyn_rows, load_rows


@pytest.fixture
def client(monkeypatch, provider, offline_pages):
    """A TestClient whose generation path cannot reach the network.

    Instructors are overlaid from the recorded department pages, because
    without them every lecture has instructor None, `_score_professor` returns
    a constant 0.5, and any test that leans on the professor factor silently
    proves nothing.
    """
    from combined_instructors import CombinedInstructors
    from cs_instructors import CSInstructors
    from ma_instructors import MAInstructors

    provider._instructors = CombinedInstructors(MAInstructors(), CSInstructors())
    monkeypatch.setattr(api, "store", api._Store())

    class StubProvider:
        def __init__(self, *args, **kwargs):
            pass

        def get_courses(self, term, codes):
            return provider.get_courses(term, codes)

    class StubRatings:
        def __init__(self, *args, **kwargs):
            pass

        def ratings_for(self, wanted):
            # Deliberately varied. A constant rating makes the professor
            # factor inert, and a test that weights it heavily would then
            # prove nothing while appearing to pass.
            return {name: 1.0 + (len(name) % 5) for name in wanted}

    monkeypatch.setattr("nyu_provider.NYUProvider", StubProvider)
    monkeypatch.setattr("ratings.RMPRatings", StubRatings)
    return TestClient(api.app)


def generate(client, courses=("MATH-UA 121", "CSCI-UA 102")):
    response = client.post("/generate", json={"term": "fall2026",
                                              "courses": list(courses)})
    assert response.status_code == 200, response.text
    return response.json()


# --- generate ----------------------------------------------------------------

def test_generate_reports_what_was_loaded(client):
    body = generate(client)
    assert body["generation_id"]
    assert {c["code"] for c in body["courses"]} == {"MATH-UA 121", "CSCI-UA 102"}
    assert body["total_schedules"] > 0
    assert body["missing"] == []


def test_a_course_with_no_sections_is_reported_not_swallowed(client):
    body = generate(client, ["MATH-UA 121", "MATH-UA 9999"])
    assert body["missing"] == ["MATH-UA 9999"]


def test_generate_is_cached_by_term_and_courses(client, monkeypatch):
    """The 7-second path must run once. Course order must not defeat the key."""
    calls = []
    original = api.generate_schedules
    monkeypatch.setattr(api, "generate_schedules",
                        lambda courses: calls.append(1) or original(courses))

    generate(client, ["MATH-UA 121", "CSCI-UA 102"])
    generate(client, ["CSCI-UA 102", "MATH-UA 121"])
    assert len(calls) == 1


def test_generate_rejects_an_empty_course_list(client):
    assert client.post("/generate", json={"term": "fall2026",
                                          "courses": []}).status_code == 422


def test_a_lectureless_lab_course_generates_schedules(client):
    """CM-UY 1001 is lab + recitation blocks with no lecture. It used to bundle
    to nothing, which zeroed every course list containing it."""
    response = client.post("/generate", json={"term": "spring2027",
                                              "courses": ["CM-UY 1001",
                                                          "CM-UY 1003"]})
    assert response.status_code == 200, response.text
    assert response.json()["total_schedules"] > 0


def test_an_unbundleable_course_is_named_not_reported_as_zero(client, monkeypatch):
    """A course with no registrable bundle empties the cartesian product. The
    student needs to know which course did it, not just that nothing fit."""
    monkeypatch.setattr(api, "unbundleable", lambda courses: ["MATH-UA 121"])
    response = client.post("/generate", json={"term": "fall2026",
                                              "courses": ["MATH-UA 121"]})
    assert response.status_code == 422
    assert "MATH-UA 121" in response.json()["detail"]


def test_an_upstream_failure_is_a_readable_502_the_browser_can_see(
        client, provider, monkeypatch):
    """Regression from the deployed instance: NYU's search failing raised
    straight out of /generate. An unhandled exception is answered outside the
    CORS middleware, so the 500 carried no allow-origin header and the browser
    reported only "Failed to fetch" -- for every request, whatever the cause."""
    import requests

    def refused(term, codes):
        raise requests.HTTPError("403 Client Error: Forbidden")

    monkeypatch.setattr(provider, "get_courses", refused)
    origin = api.ALLOWED_ORIGINS[0]
    response = client.post("/generate", headers={"Origin": origin},
                           json={"term": "spring2027", "courses": ["CM-UY 1001"]})

    assert response.status_code == 502
    assert "403" in response.json()["detail"]
    assert response.headers["access-control-allow-origin"] == origin


def test_generate_rejects_an_unknown_term(client):
    assert client.post("/generate", json={"term": "winter2026",
                                          "courses": ["MATH-UA 121"]}).status_code == 422


# --- rank --------------------------------------------------------------------

def rank(client, generation_id, **overrides):
    payload = {"generation_id": generation_id, "preferred_days": ["MON", "WED"],
               "time_start": "09:00", "time_end": "18:00", "limit": 5}
    payload.update(overrides)
    response = client.post("/rank", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_rank_returns_labelled_alternatives(client):
    body = rank(client, generate(client)["generation_id"])
    assert body["eligible"] > 0
    assert body["alternatives"]
    assert body["alternatives"][0]["reason"] == "best overall"
    assert all(a["sections"] for a in body["alternatives"])


def test_sections_carry_everything_the_ui_needs_to_render_a_week(client):
    body = rank(client, generate(client)["generation_id"])
    section = next(s for a in body["alternatives"] for s in a["sections"]
                   if s["meetings"])
    meeting = section["meetings"][0]

    assert section["section_id"]          # the CRN to register with
    assert 0 <= meeting["day"] <= 6       # grid column
    assert meeting["end"] > meeting["start"]
    # Pre-formatted so the browser never re-derives a clock time.
    assert ":" in meeting["start_label"]


def test_an_unknown_generation_id_fails_loudly(client):
    response = client.post("/rank", json={"generation_id": "nope"})
    assert response.status_code == 404


def test_an_unknown_factor_name_is_rejected(client):
    generation_id = generate(client)["generation_id"]
    response = client.post("/rank", json={"generation_id": generation_id,
                                          "weights": {"vibes": 5}})
    assert response.status_code == 422


def test_an_unknown_day_is_rejected(client):
    generation_id = generate(client)["generation_id"]
    response = client.post("/rank", json={"generation_id": generation_id,
                                          "preferred_days": ["FUNDAY"]})
    assert response.status_code == 422


def test_requiring_a_course_that_was_not_requested_is_rejected(client):
    """Vacuously ignoring a stated non-negotiable is the failure this catches;
    the CLI learned the same lesson."""
    generation_id = generate(client)["generation_id"]
    response = client.post("/rank", json={
        "generation_id": generation_id,
        "required": {"PHYS-UA 11": "Someone"}})
    assert response.status_code == 422


def test_weights_change_the_ranking_without_regenerating(client, monkeypatch):
    """The point of the split: re-ranking is the interactive path and must not
    re-enter generation."""
    generation_id = generate(client)["generation_id"]
    calls = []
    monkeypatch.setattr(api, "generate_schedules", lambda c: calls.append(1))

    days_first = rank(client, generation_id, weights={"days_on_campus": 100})
    prof_first = rank(client, generation_id, weights={"professor": 100})

    assert calls == [], "ranking must not regenerate"
    assert days_first["alternatives"][0]["sections"] != \
        prof_first["alternatives"][0]["sections"]


def test_the_filtered_set_is_reused_across_preference_changes(client, monkeypatch):
    """Constraints change on a click, preferences on a drag. Filtering is the
    2.6s step, so it must not run again just because a slider moved."""
    generation_id = generate(client)["generation_id"]
    calls = []
    original = api.best_effort
    monkeypatch.setattr(api, "best_effort",
                        lambda s, r, p: calls.append(1) or original(s, r, p))

    rank(client, generation_id, time_start="09:00")
    rank(client, generation_id, time_start="11:00")
    rank(client, generation_id, weights={"time": 9})
    assert len(calls) == 1

    # Changing a constraint is a different key and must recompute.
    rank(client, generation_id, commute_minutes=30)
    assert len(calls) == 2


def test_narration_is_off_by_default(client):
    body = rank(client, generate(client)["generation_id"])
    assert all(a["explanation"] is None for a in body["alternatives"])


# --- metadata ----------------------------------------------------------------

def test_factors_endpoint_describes_the_sliders(client):
    body = client.get("/factors").json()
    names = {f["name"] for f in body["factors"]}
    assert "professor" in names and "days_on_campus" in names
    assert "fall2026" in body["terms"]
    assert "MON" in body["days"]


def test_ratings_reach_the_client_attached_to_sections(client):
    """The UI shows a rating next to a professor's name, so it has to travel
    with the section rather than needing a second lookup."""
    body = generate(client)
    assert body["ratings"], "expected instructor ratings to be resolved"

    ranked = rank(client, body["generation_id"])
    rated = [s for a in ranked["alternatives"] for s in a["sections"]
             if s["instructor"]]
    assert rated, "expected at least one named instructor"
    assert any(s["rating"] is not None for s in rated)
