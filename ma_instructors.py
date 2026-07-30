from __future__ import annotations
import re
import requests
from bs4 import BeautifulSoup

URL = "https://math.nyu.edu/dynamic/courses/course-schedules/"
HEADERS = {"User-Agent": "ScheduleCreator/0.1 (personal project)"}

# Our internal term codes mapped to Courant's semester slugs.
TERM_SLUGS = {"1268": "fall-2026", "1264": "spring-2026"}

# 'MATH-UA.0121-001' -> ('MATH-UA', '0121', '001'). Using search() means the
# first code wins on cross-listed rows like 'MATH-GA.2010-001 CSCI-GA 2420.001'.
CATALOG_RE = re.compile(r"([A-Z]+-[A-Z]{2})\.(\d+)-(\S+)")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _value(cell_text: str) -> str:
    """Cells embed their own label: 'Instructor(s): Fanny Shum'."""
    return cell_text.split(":", 1)[1].strip() if ":" in cell_text else cell_text


class MAInstructors:
    """Instructor names keyed by (course_code, section_no), scraped from the
    Courant (Math department) course-schedule page.

    Same (course_code, section_no) -> name shape as CSInstructors, so
    NYUProvider's enrichment pass doesn't need to know which department a
    name came from; the two sources are interchangeable inputs to it.
    Courant publishes only primary lecture sections, which is exactly what
    the professor factor scores.
    """

    def __init__(self):
        self._cache: dict[str, dict[tuple[str, str], str]] = {}

    def instructors_for(self, term: str) -> dict[tuple[str, str], str]:
        if term in self._cache:
            return self._cache[term]

        slug = TERM_SLUGS.get(term)
        if slug is None:
            print(f"warning: no Math department semester slug for term {term!r}")
            self._cache[term] = {}
            return {}

        html = requests.get(URL, params={"semester": slug},
                            headers=HEADERS, timeout=30).text
        soup = BeautifulSoup(html, "html.parser")
        # The table repeats its headers inside each cell for mobile layout.
        for span in soup.select("span.header-text"):
            span.decompose()

        found: dict[tuple[str, str], str] = {}
        table = soup.find("table")
        for tr in (table.find_all("tr") if table else []):
            cells = [_clean(td.get_text()) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            match = CATALOG_RE.search(cells[0])
            if not match:
                continue
            subject, number, section_no = match.groups()
            code = f"{subject} {int(number)}"   # '0121' -> 121, drops padding
            instructor = _value(cells[2])
            if instructor:
                found[(code, section_no)] = instructor

        self._cache[term] = found
        return found