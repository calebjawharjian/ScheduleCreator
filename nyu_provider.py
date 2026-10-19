from __future__ import annotations
import json
import re
import requests

from models import Course, Section, SectionKind, Meeting, Day
from providers import CourseProvider
from campuses import BROOKLYN_CAMP_CODES, BROOKLYN, WASHINGTON_SQUARE

URL = "https://bulletins.nyu.edu/class-search/api/"
HEADERS = {"User-Agent": "ScheduleCreator/0.1 (personal project)"}

# NYU's component codes mapped onto our own enum.
KIND_MAP = {
    "LEC": SectionKind.LECTURE,
    "RCT": SectionKind.RECITATION,
    "LAB": SectionKind.LAB,
}

# Tandon numbers sections as block stem + component code: 'A1LC' is the lecture
# of block A1, 'A1LB' its lab, 'A1RC' its recitation. The stem is the real
# grouping key, and it does not depend on ordering the way CAS numbering does.
BLOCK_STEM_RE = re.compile(r"([A-Z]+\d+)[A-Z]{2}")


def _block_stem(section_no: str | None) -> str | None:
    """'A1LB' -> 'A1'. None for CAS-style numeric sections like '001'."""
    match = BLOCK_STEM_RE.fullmatch(section_no or "")
    return match.group(1) if match else None


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

    def _search_subject(self, term: str, subject: str,
                        camp: str | None = None) -> list[dict]:
        """Rows for one subject, optionally narrowed to a location.

        `camp` is part of the cache key, since the same subject returns
        different rows filtered and unfiltered.
        """
        key = (term, subject, camp)
        if key in self._cache:
            return self._cache[key]

        params = {"page": "fose", "route": "search", "subject": subject}
        criteria = [{"field": "subject", "value": subject}]
        if camp:
            criteria.append({"field": "camp", "value": camp})
        payload = {"other": {"srcdb": term}, "criteria": criteria}
        response = requests.post(URL, params=params, data=json.dumps(payload),
                                 headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])

        self._cache[key] = results
        return results

    def _brooklyn_crns(self, term: str, subject: str) -> set[str]:
        """CRNs meeting in Brooklyn, per NYU's own location filter.

        One extra request per subject. Worth it: campus cannot be derived from
        the course code, because NYU runs some Washington Square-coded courses
        in Brooklyn. Only Brooklyn is queried -- see campuses.py for why the
        Washington Square filter cannot serve as its complement.
        """
        try:
            rows = self._search_subject(term, subject, camp=BROOKLYN_CAMP_CODES)
        except requests.RequestException as exc:
            print(f"warning: could not determine Brooklyn sections for "
                  f"{subject}: {exc}; campus left unknown")
            return set()
        return {row["crn"] for row in rows}

    def _section_order(self, row: dict) -> tuple:
        """Sort key for section numbers like '001', '011a'. Digits first,
        so ordering is numeric rather than lexicographic."""
        no = row.get("no", "")
        digits = "".join(c for c in no if c.isdigit())
        return (int(digits) if digits else 0, no)

    def _link_sections(self, code: str, usable: list[dict]) -> dict[str, str | None]:
        """Map each non-lecture section's CRN to the CRN of its lecture.

        Three strategies, tried in order of how much they assume:

        * **Single lecture.** If the course has exactly one lecture, every
          non-lecture section belongs to it. This is not an inference at all --
          there is no other lecture it could belong to -- so it goes first and
          overrides the schemes below. It also rescues numbering neither scheme
          parses: CS-UY 2204's sections are 'ALAB', 'BLAB', 'CLAB', 'DLAB' and
          'LEC', which carry no digits, so `_block_stem` returns None for all of
          them and `_section_order` ties them all at 0 -- sorting the labs
          *before* the lecture lexicographically and orphaning all four.
        * **Stem-based** (Tandon: 'A1LC' lecture, 'A1LB' lab, 'A1RC' recitation).
          The block is named explicitly, so linkage is a lookup and ordering is
          irrelevant. Required here: the component suffixes sort 'A1LB' before
          'A1LC', so a sequential scan attaches every lab to the *previous*
          block's lecture, silently and undetectably.
        * **Sequential** (CAS: '001' lecture, '002'-'005' recitations). No block
          is named, so a lecture starts a block and the following non-lectures
          belong to it. Still an inference; see tests/test_live_data.py.

        Stems are used only when *every* section has one, so a course with mixed
        or unrecognized numbering falls back rather than half-applying a scheme.
        """
        stems = {row["crn"]: _block_stem(row.get("no")) for row in usable}
        parents: dict[str, str | None] = {}
        lectures = [row for row in usable
                    if KIND_MAP[row["schd"]] == SectionKind.LECTURE]

        if self._blocks(usable):
            # No lecture exists to link to, by design; `_blocks` groups them.
            return parents

        if len(lectures) == 1:
            only = lectures[0]["crn"]
            for row in usable:
                if KIND_MAP[row["schd"]] != SectionKind.LECTURE:
                    parents[row["crn"]] = only
        elif usable and all(stems.values()):
            lecture_by_stem: dict[str, str] = {}
            for row in usable:
                if KIND_MAP[row["schd"]] == SectionKind.LECTURE:
                    lecture_by_stem[stems[row["crn"]]] = row["crn"]
            for row in usable:
                if KIND_MAP[row["schd"]] != SectionKind.LECTURE:
                    parents[row["crn"]] = lecture_by_stem.get(stems[row["crn"]])
        else:
            current_lecture_id: str | None = None
            for row in usable:
                if KIND_MAP[row["schd"]] == SectionKind.LECTURE:
                    current_lecture_id = row["crn"]
                else:
                    parents[row["crn"]] = current_lecture_id

        for row in usable:
            if KIND_MAP[row["schd"]] == SectionKind.LECTURE:
                continue
            if parents.get(row["crn"]) is None:
                print(f"warning: {code} section {row.get('no')} "
                      f"({row['schd']}) has no lecture to link to; unlinked")

        return parents

    def _blocks(self, usable: list[dict]) -> dict[str, str]:
        """CRN -> block stem, for a course with no lecture at all.

        CM-UY 1001 (chem lab) is seven blocks of 'A1LB' lab + 'A1RC'
        recitation; its lecture is a separate course, CM-UY 1003. With nothing
        to link to, every section used to be dropped as an orphan, the course
        had no bundles, and any list containing it produced 0 schedules.

        Only stem numbering qualifies, for the same reason as in
        `_link_sections`: the block is named, so this is a lookup. A lectureless
        course with numeric sections names no block, so it is left ungrouped
        and reported by `scheduler.unbundleable` rather than guessed at.
        """
        if not usable or any(KIND_MAP[row["schd"]] == SectionKind.LECTURE
                             for row in usable):
            return {}
        stems = {row["crn"]: _block_stem(row.get("no")) for row in usable}
        return stems if all(stems.values()) else {}

    def get_courses(self, term: str, course_codes: list[str]) -> list[Course]:
        wanted = set(course_codes)
        subjects = {_subject_of(code) for code in wanted}

        rows: list[dict] = []
        brooklyn_crns: set[str] = set()
        for subject in subjects:
            rows.extend(self._search_subject(term, subject))
            brooklyn_crns |= self._brooklyn_crns(term, subject)

        by_code: dict[str, list[dict]] = {}
        for row in rows:
            if row["code"] in wanted and not row.get("isCancelled"):
                by_code.setdefault(row["code"], []).append(row)

        courses: dict[str, Course] = {}
        for code, course_rows in by_code.items():
            course = Course(code=code, title=course_rows[0].get("title", ""))
            course_rows.sort(key=self._section_order)

            usable: list[dict] = []
            for row in course_rows:
                schd = row.get("schd", "")
                if schd not in KIND_MAP:
                    print(f"warning: skipping unknown section type {schd!r} "
                          f"for {code} section {row.get('no')}")
                    continue
                usable.append(row)

            parents = self._link_sections(code, usable)
            blocks = self._blocks(usable)

            for row in usable:
                kind = KIND_MAP[row["schd"]]
                course.sections.append(Section(
                    section_id=row["crn"],
                    course_code=code,
                    kind=kind,
                    instructor=row.get("instr") or None,
                    meetings=_parse_meetings(row.get("meetingTimes", "")),
                    parent_section_id=parents.get(row["crn"]),
                    section_no=row.get("no"),
                    campus=(BROOKLYN if row["crn"] in brooklyn_crns
                            else WASHINGTON_SQUARE),
                    block_id=blocks.get(row["crn"]),
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