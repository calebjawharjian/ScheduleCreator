"""Professor ratings, kept behind an interface.

RateMyProfessor has no official API. This talks to the same internal GraphQL
endpoint the public site uses, which is very likely against their terms of
service -- acceptable for a personal project, a real question if this ever
ships. `RatingProvider` exists so that stays a swappable decision rather than
something baked into scoring.

The join here is fundamentally weaker than the Math/CS instructor joins. Those
match on `(course_code, section_no)`, an identifier both sides agree on. NYU's
catalog and RMP share nothing but a human name, so this is the one place in the
project doing genuine entity matching -- and the one place most likely to
attach the wrong number to the right person.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import json
import re
import time
from pathlib import Path

import requests

URL = "https://www.ratemyprofessors.com/graphql"
# The anonymous credential the public site ships in its own client bundle.
HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",
    "Content-Type": "application/json",
    "User-Agent": "ScheduleCreator/0.1 (personal project)",
}

# 'New York University'. Searching the name also returns NYU Wagner and NYU
# Los Angeles, which are separate schools with separate faculty, so this is
# pinned rather than looked up.
NYU_SCHOOL_ID = "U2Nob29sLTY3NQ=="

TEACHER_QUERY = """
query($text: String!, $school: ID!) {
  newSearch {
    teachers(query: {text: $text, schoolID: $school}, first: 12) {
      edges { node { firstName lastName department avgRating numRatings } }
    }
  }
}
"""

# Bayesian shrinkage toward the prior. A 5.0 from one review is weak evidence;
# a 4.3 from a hundred is strong. Without this a single glowing review outranks
# an established record, which is the exact failure this factor must avoid.
PRIOR_MEAN = 3.5
PRIOR_WEIGHT = 10

# Course subject -> RMP department, used only to break ties between people who
# share a name. Extend as new departments are added.
SUBJECT_DEPARTMENTS = {
    "MATH-UA": "Mathematics",
    "MATH-GA": "Mathematics",
    "CSCI-UA": "Computer Science",
    "CSCI-GA": "Computer Science",
}


def shrink(average: float, count: int,
           prior: float = PRIOR_MEAN, weight: float = PRIOR_WEIGHT) -> float:
    """Pull a rating toward the prior in proportion to how little data backs it."""
    return (count * average + weight * prior) / (count + weight)


def _norm(text: str | None) -> str:
    """Casefold and collapse whitespace. RMP's own data carries stray double
    spaces and trailing spaces inside firstName ('Tobias  Blickhan')."""
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _first_last(full_name: str) -> tuple[str, str]:
    """'Hilbert Gene Locklear' -> ('hilbert', 'locklear').

    Middle names are dropped because NYU's catalog includes them and RMP
    generally does not, so matching on the full string would miss.
    """
    tokens = _norm(full_name).split()
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return "", tokens[0]
    return tokens[0], tokens[-1]


def department_hint(course_code: str) -> str | None:
    """'CSCI-UA 102' -> 'Computer Science'."""
    return SUBJECT_DEPARTMENTS.get(course_code.split()[0])


class RatingProvider(ABC):
    """A source of instructor ratings on a 0-5 scale."""

    @abstractmethod
    def ratings_for(self, wanted: dict[str, str | None]) -> dict[str, float]:
        """Map {instructor name -> department hint} to {name -> 0..5 rating}.

        Names that cannot be matched confidently are simply absent from the
        result, which `_score_professor` already treats as neutral.
        """
        ...


class NullRatings(RatingProvider):
    """No ratings at all. The offline/default behavior."""

    def ratings_for(self, wanted: dict[str, str | None]) -> dict[str, float]:
        return {}


class RMPRatings(RatingProvider):
    """Reads ratings from RateMyProfessor's internal GraphQL endpoint."""

    def __init__(self, school_id: str = NYU_SCHOOL_ID, delay: float = 1.0,
                 cache_path: str | Path | None = ".rmp_cache.json",
                 verbose: bool = True):
        self._school_id = school_id
        self._delay = delay          # be polite; this is not an API we're granted
        self._verbose = verbose
        self._cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, list[dict]] = {}
        if self._cache_path and self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text())
            except (OSError, ValueError):
                self._cache = {}   # a corrupt cache should never break a run

    # --- network ---------------------------------------------------------

    def _search(self, text: str) -> list[dict]:
        """Candidate teachers for a search string, cached across runs."""
        key = _norm(text)
        if key in self._cache:
            return self._cache[key]

        if self._delay:
            time.sleep(self._delay)
        response = requests.post(
            URL, headers=HEADERS, timeout=30,
            json={"query": TEACHER_QUERY,
                  "variables": {"text": text, "school": self._school_id}},
        )
        response.raise_for_status()
        payload = response.json()
        edges = (payload.get("data", {}).get("newSearch", {})
                 .get("teachers") or {}).get("edges", [])
        nodes = [edge["node"] for edge in edges]

        self._cache[key] = nodes
        self._save_cache()
        return nodes

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.write_text(json.dumps(self._cache))
        except OSError:
            pass   # caching is an optimization, never a hard requirement

    # --- matching --------------------------------------------------------

    def _match(self, full_name: str, hint: str | None,
               candidates: list[dict]) -> tuple[dict | None, str | None]:
        """Pick the one candidate that is confidently this person.

        Strict by design: a rating silently attached to the wrong professor is
        worse than no rating, because it actively misranks schedules instead of
        staying neutral. Anything ambiguous returns None with a reason.
        """
        first, last = _first_last(full_name)
        named = [c for c in candidates
                 if _norm(c.get("lastName")) == last
                 and _norm(c.get("firstName")) == first]
        if not named:
            return None, "no name match"

        # A profile with no reviews reports avgRating 0.0, which is emphatically
        # not a rating of zero. Treating it as one would score an unreviewed
        # professor as the worst possible.
        rated = [c for c in named if (c.get("numRatings") or 0) > 0]
        if not rated:
            return None, "no ratings yet"

        if len(rated) > 1 and hint:
            narrowed = [c for c in rated if _norm(c.get("department")) == _norm(hint)]
            if len(narrowed) == 1:
                rated = narrowed

        if len(rated) > 1:
            return None, f"ambiguous ({len(rated)} candidates)"
        return rated[0], None

    # --- interface -------------------------------------------------------

    def ratings_for(self, wanted: dict[str, str | None]) -> dict[str, float]:
        ratings: dict[str, float] = {}
        for name, hint in sorted(wanted.items()):
            if not name:
                continue
            try:
                candidates = self._search(_first_last(name)[1] or name)
            except requests.RequestException as exc:
                if self._verbose:
                    print(f"warning: rating lookup failed for {name}: {exc}")
                continue

            match, reason = self._match(name, hint, candidates)
            if match is None:
                if self._verbose:
                    print(f"warning: no rating for {name} ({reason})")
                continue

            ratings[name] = shrink(float(match["avgRating"]),
                                   int(match["numRatings"]))
        return ratings
