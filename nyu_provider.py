from __future__ import annotations
import json
import requests

from models import Course, Section, SectionKind, Meeting, Day
from providers import CourseProvider

URL = "https://bulletins.nyu.edu/class-search/api/"
HEADERS = {"User-Agent": "ScheduleCreator/0.1 (personal project)"}

# NYU's component codes mapped onto our own enum.
KIND_MAP = {
    "LEC": SectionKind.LECTURE,
    "RCT": SectionKind.RECITATION,
    "LAB": SectionKind.LAB,
}


def _subject_of(course_code: str) -> str:
    """'CSCI-UA 102' -> 'CSCI-UA'. The API searches by subject, not by course."""
    return course_code.split()[0]


def _to_minutes(hhmm_text: str) -> int:
    """'1400' -> 840. Pads first, since '930' can appear instead of '0930'."""
    padded = hhmm_text.zfill(4)
    return int(padded[:2]) * 60 + int(padded[2:])


def _parse_meetings(raw: str) -> list[Meeting]:
    if not raw:
        return []
    return [
        Meeting(
            day=Day(int(entry["meet_day"])),
            start=_to_minutes(entry["start_time"]),
            end=_to_minutes(entry["end_time"]),
        )
        for entry in json.loads(raw)   # note: it's a JSON string inside the JSON
    ]


class NYUProvider(CourseProvider):
    """Reads live course data from NYU's public class search API."""

    def __init__(self, instructors=None):
            self._cache: dict[tuple[str, str], list[dict]] = {}
            self._instructors = instructors

    def _search_subject(self, term: str, subject: str) -> list[dict]:
        if (term, subject) in self._cache:
            return self._cache[(term, subject)]

        params = {"page": "fose", "route": "search", "subject": subject}
        payload = {
            "other": {"srcdb": term},
            "criteria": [{"field": "subject", "value": subject}],
        }
        response = requests.post(URL, params=params, data=json.dumps(payload),
                                 headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])

        self._cache[(term, subject)] = results
        return results

    def _section_order(self, row: dict) -> tuple:
        """Sort key for section numbers like '001', '011a'. Digits first,
        so ordering is numeric rather than lexicographic."""
        no = row.get("no", "")
        digits = "".join(c for c in no if c.isdigit())
        return (int(digits) if digits else 0, no)

    def get_courses(self, term: str, course_codes: list[str]) -> list[Course]:
        wanted = set(course_codes)
        subjects = {_subject_of(code) for code in wanted}

        rows: list[dict] = []
        for subject in subjects:
            rows.extend(self._search_subject(term, subject))

        by_code: dict[str, list[dict]] = {}
        for row in rows:
            if row["code"] in wanted and not row.get("isCancelled"):
                by_code.setdefault(row["code"], []).append(row)

        courses: dict[str, Course] = {}
        for code, course_rows in by_code.items():
            course = Course(code=code, title=course_rows[0].get("title", ""))
            course_rows.sort(key=self._section_order)

            current_lecture_id: str | None = None
            for row in course_rows:
                schd = row.get("schd", "")
                if schd not in KIND_MAP:
                    print(f"warning: skipping unknown section type {schd!r} "
                          f"for {code} section {row.get('no')}")
                    continue

                kind = KIND_MAP[schd]
                parent = None
                if kind == SectionKind.LECTURE:
                    current_lecture_id = row["crn"]
                else:
                    parent = current_lecture_id
                    if parent is None:
                        print(f"warning: {code} section {row.get('no')} "
                              f"({schd}) appears before any lecture; unlinked")

                course.sections.append(Section(
                    section_id=row["crn"],
                    course_code=code,
                    kind=kind,
                    instructor=row.get("instr") or None,
                    meetings=_parse_meetings(row.get("meetingTimes", "")),
                    parent_section_id=parent,
                    section_no=row.get("no"),          # <-- added
                ))

            courses[code] = course

        # Enrichment pass: overlay department-published instructor names.  <-- added block
        if self._instructors is not None:
            lookup = self._instructors.instructors_for(term)
            for course in courses.values():
                for s in course.sections:
                    name = lookup.get((course.code, s.section_no))
                    if name:
                        s.instructor = name

        return [courses[c] for c in course_codes if c in courses]