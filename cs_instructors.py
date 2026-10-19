from __future__ import annotations
import re
import requests
from bs4 import BeautifulSoup

URL = "https://cs.nyu.edu/dynamic/courses/schedule/"
HEADERS = {"User-Agent": "ScheduleCreator/0.1 (personal project)"}

# Our internal term codes mapped to the CS department's semester slugs.
# Note the underscore, unlike Courant's dashed 'fall-2026'.
TERM_SLUGS = {"1268": "fall_2026", "1264": "spring_2026", "1274": "spring_2027"}

# 'CSCI-UA.0101-001' -> ('CSCI-UA', '0101', '001'). Same shape as Courant's
# catalog format, so the same search()-wins-on-cross-listings logic applies.
CATALOG_RE = re.compile(r"([A-Z]+-[A-Z]{2})\.(\d+)-(\S+)")

# Placeholder text this page uses in place of a real instructor name.
NON_NAMES = {"TBA", "STAFF", "CANCELLED"}


def _clean(text: str) -> str:
    # The catalog anchor embeds a zero-width space before the section
    # number ('0101-​001'), invisible but not whitespace to regex \s.
    return re.sub(r"\s+", " ", text.replace("​", "")).strip()


class CSInstructors:
    """Instructor names keyed by (course_code, section_no), scraped from the
    CS department's own schedule page.

    Same (course_code, section_no) -> name shape as MAInstructors, so
    NYUProvider's enrichment pass doesn't need to know which department a
    name came from; the two sources are interchangeable inputs to it.
    """

    def __init__(self):
        self._cache: dict[str, dict[tuple[str, str], str]] = {}

    def instructors_for(self, term: str) -> dict[tuple[str, str], str]:
        if term in self._cache:
            return self._cache[term]

        slug = TERM_SLUGS.get(term)
        if slug is None:
            print(f"warning: no CS department semester slug for term {term!r}")
            self._cache[term] = {}
            return {}

        html = requests.get(URL, params={"semester": slug},
                            headers=HEADERS, timeout=30).text
        soup = BeautifulSoup(html, "html.parser")

        found: dict[tuple[str, str], str] = {}
        for row in soup.select("li.row"):
            catalog_span = row.select_one("span.italic")
            if not catalog_span:
                continue
            match = CATALOG_RE.search(_clean(catalog_span.get_text()))
            if not match:
                continue
            subject, number, section_no = match.groups()
            code = f"{subject} {int(number)}"   # '0101' -> 101, drops padding

            # Professor, meet time, and room share one class and appear in
            # that fixed order; professor is always first.
            fields = row.select("span.col-xs-12.col-sm-2")
            if not fields:
                continue
            instructor = _clean(fields[0].get_text())
            if instructor and instructor.upper() not in NON_NAMES:
                found[(code, section_no)] = instructor

        self._cache[term] = found
        return found
