"""Shared fixtures. Everything here is offline and deterministic.

The recorded fixtures are real responses, not hand-written mocks, because every
serious bug in this project came from real-world weirdness that invented data
would have smoothed over: `meetingTimes` being a JSON string nested inside JSON,
a literal zero-width space in the CS catalog anchors, and 'A1LB' sorting before
'A1LC'. Synthetic data would pass tests the real thing fails.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Which recorded page answers which host, so one fake serves both scrapers.
PAGES = {
    "cs.nyu.edu": FIXTURES / "cs_schedule_fall_2026.html",
    "math.nyu.edu": FIXTURES / "math_schedule_fall_2026.html",
}

FALL_2026 = "1268"
SPRING_2027 = "1274"

# Recorded subjects per term. CM-UY is Spring 2027 because that is where its
# lectureless lab course (CM-UY 1001) was found.
RECORDED = {FALL_2026: ("MATH-UA", "CSCI-UA", "EG-UY", "CS-UY"),
            SPRING_2027: ("CM-UY",)}


def load_rows(subject: str, term: str = FALL_2026) -> list[dict]:
    """Recorded class-search rows for one subject, as the API returned them."""
    return json.loads((FIXTURES / f"search_{subject}_{term}.json").read_text())


def load_brooklyn_rows(subject: str, term: str = FALL_2026) -> list[dict]:
    """The same search narrowed to NYU's Brooklyn location filter.

    Recorded separately because campus cannot be derived from the course code:
    MATH-UA 140 sections 021 and 022 meet in Brooklyn while its other 30
    sections are at Washington Square.
    """
    return json.loads((FIXTURES / f"brooklyn_{subject}_{term}.json").read_text())


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Fail loudly if any test reaches for the network.

    Without this, a future change that bypasses a fixture would still pass --
    just slowly, and only while NYU keeps the term online. Tests that need page
    content override `requests.get` via the offline_pages fixture; tests marked
    `live` are exempt because reaching NYU is the whole point of them.
    """
    if request.node.get_closest_marker("live"):
        return

    import requests

    def blocked(*args, **kwargs):
        raise AssertionError(
            "test attempted a real HTTP request; use a recorded fixture")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)


@pytest.fixture
def provider():
    """An NYUProvider that can never reach the network.

    Pre-populating `_cache` rather than mocking `requests` means the whole real
    code path still runs -- filtering, numeric sorting, kind mapping, linkage,
    meeting parsing. Only the fetch is skipped, and no production code needed a
    seam added for testability.
    """
    from nyu_provider import NYUProvider
    from campuses import BROOKLYN_CAMP_CODES

    p = NYUProvider()
    for term, subjects in RECORDED.items():
        for subject in subjects:
            # The cache key includes the location filter, since the same
            # subject returns different rows filtered and unfiltered.
            p._cache[(term, subject, None)] = load_rows(subject, term)
            p._cache[(term, subject, BROOKLYN_CAMP_CODES)] = \
                load_brooklyn_rows(subject, term)

    def cached_only(term, subject, camp=None):
        key = (term, subject, camp)
        if key not in p._cache:
            raise AssertionError(f"no recorded fixture for {key}")
        return p._cache[key]

    p._search_subject = cached_only
    return p


@pytest.fixture
def offline_pages(monkeypatch):
    """Point `requests.get` at recorded department HTML, dispatched by host.

    Both scraper modules do `import requests`, so they share one module object;
    patching per-module would have the second patch clobber the first. Routing
    on the URL keeps both usable in the same test.
    """
    import requests

    class Response:
        def __init__(self, text: str):
            self.text = text

    def fake_get(url, **kwargs):
        for host, path in PAGES.items():
            if host in url:
                return Response(path.read_text(encoding="utf-8"))
        raise AssertionError(f"unexpected URL in test: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
