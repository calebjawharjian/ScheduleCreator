from __future__ import annotations
import functools
import json
import os
import re
import tempfile
from pathlib import Path

import certifi
import requests

from models import Course, Section, SectionKind, Meeting, Day
from patterns import CoursePattern
from providers import CourseProvider
from campuses import BROOKLYN_CAMP_CODES, BROOKLYN, WASHINGTON_SQUARE

URL = "https://bulletins.nyu.edu/class-search/api/"
HEADERS = {"User-Agent": "ScheduleCreator/0.1 (personal project)"}

EMSIGN_ROOT = Path(__file__).parent / "certs" / "emsign_root_ca_g1.pem"

# '4 Hour Lecture' -> 4. Anchored at the start so a range like '1 - 4 Hour'
# fails to match and is reported, rather than read as 1.
CREDITS_RE = re.compile(r"(\d+(?:\.\d+)?) Hours? ")

def _topics(titles: list[str]) -> dict[str, str]:
    """Each sibling title -> the part that tells it apart.

    Derived from the titles themselves, because departments spell topics
    every which way (Spring 2027): 'Special Topics in Computer Science:
    Computer Graphics', 'Spec Topics Psych:: Computational Cognitive Science',
    'Texts & Ideas: Topics: A Genealogy of Morality'. Splitting on the first
    colon fit only the first. Instead: strip the prefix every sibling shares,
    cut back to its last colon -- otherwise Graphics, Networks and Vision,
    which all begin 'Computer ', would come out as 'Graphics'. A title with
    nothing left after that ('Topics:', or a bare 'Topics in Middle Eastern
    History' beside its subtitled siblings) keeps its full title.
    """
    shared = os.path.commonprefix(titles)
    cut = shared.rfind(":") + 1          # 0 when the shared part has no colon
    if shared in titles:
        # A bare heading listed beside its subtitled siblings is the heading
        # in full, colon or not: 'Topics in Middle Eastern History'.
        cut = max(cut, len(shared))
    topics = {}
    for title in titles:
        rest = title[cut:].lstrip(": ").strip()
        topics[title] = rest or title
    return topics


@functools.cache
def _nyu_ca_bundle() -> str:
    """certifi's current roots plus emSign Root CA - G1, for NYU's requests only.

    NYU's class search chains to emSign, which certifi 2026.10.18 dropped. It
    worked on any machine with an older certifi and failed on every fresh
    install -- which is to say, only once deployed. Appending to certifi's
    bundle rather than replacing it keeps working if NYU moves to another CA,
    and scoping it here means no other host trusts emSign.
    """
    bundle = Path(tempfile.gettempdir()) / "schedulecreator_nyu_ca.pem"
    bundle.write_text(Path(certifi.where()).read_text() + "\n"
                      + EMSIGN_ROOT.read_text())
    return str(bundle)

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
            self._details_cache: dict[tuple[str, str], dict] = {}
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
                                 headers=HEADERS, timeout=30,
                                 verify=_nyu_ca_bundle())
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

    def _details(self, term: str, code: str, crn: str) -> dict:
        """The per-section details record. One request, cached by CRN."""
        key = (term, crn)
        if key not in self._details_cache:
            payload = {"group": f"code:{code}", "key": f"crn:{crn}",
                       "srcdb": term, "matched": f"crn:{crn}"}
            response = requests.post(
                URL, params={"page": "fose", "route": "details"},
                data=json.dumps(payload), headers=HEADERS, timeout=30,
                verify=_nyu_ca_bundle())
            response.raise_for_status()
            self._details_cache[key] = response.json()
        return self._details_cache[key]

    def _credits(self, term: str, code: str, crn: str) -> float | None:
        """A course's credits, from any one of its sections.

        `hours_html` is the *course's* credit count repeated on every section:
        MATH-UA 121's recitation reads '4 Hour Recitation' and EG-UY 1004's lab
        '4 Hour Laboratory', for 4-credit courses. So it is read once per course
        and never summed across a bundle, which would triple-count EG-UY 1004.

        None, with a warning, when it cannot be read. Credits only matter once
        a credit range is set, and that path refuses unknown credits loudly
        instead of treating them as zero.
        """
        try:
            text = self._details(term, code, crn).get("hours_html") or ""
        except requests.RequestException as exc:
            print(f"warning: could not read credits for {code}: {exc}")
            return None
        match = CREDITS_RE.match(text)
        if not match:
            print(f"warning: unrecognized credits {text!r} for {code}")
            return None
        return float(match.group(1))

    def get_courses(self, term: str, course_codes: list[str]) -> list[Course]:
        """Courses matching each code or wildcard, in request order.

        A wildcard ('CSCI-UA 4xx') returns every match. A code that NYU uses
        for several distinct courses -- CSCI-UA 480 is seven Special Topics in
        Spring 2027 -- returns one Course per topic, so Graphics and Quantum
        Computing are never offered as interchangeable sections of one class.
        """
        patterns = [CoursePattern.parse(code) for code in course_codes]
        subjects = {p.subject for p in patterns}

        rows: list[dict] = []
        brooklyn_crns: set[str] = set()
        for subject in subjects:
            rows.extend(self._search_subject(term, subject))
            brooklyn_crns |= self._brooklyn_crns(term, subject)

        # Grouped by (code, title), not code: the title is what tells Special
        # Topics apart. An ordinary course has one title across its sections.
        by_course: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            if (any(p.matches(row["code"]) for p in patterns)
                    and not row.get("isCancelled")):
                key = (row["code"], row.get("title", ""))
                by_course.setdefault(key, []).append(row)
        titles_by_code: dict[str, list[str]] = {}
        for code, title in by_course:
            titles_by_code.setdefault(code, []).append(title)
        topic_of = {(code, title): topic
                    for code, titles in titles_by_code.items() if len(titles) > 1
                    for title, topic in _topics(sorted(titles)).items()}

        courses: dict[tuple[str, str], Course] = {}
        for (code, title), course_rows in by_course.items():
            topic = topic_of.get((code, title))
            # Sorted first so credits always come from the same section,
            # whatever order the API returned rows in.
            course_rows.sort(key=self._section_order)
            course = Course(code=code, title=title, topic=topic,
                            credits=self._credits(term, code, course_rows[0]["crn"]))

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
                    topic=topic,
                ))

            courses[(code, title)] = course

        # Enrichment pass: overlay department-published instructor names.  <-- added block
        if self._instructors is not None:
            lookup = self._instructors.instructors_for(term)
            for course in courses.values():
                for s in course.sections:
                    name = lookup.get((course.code, s.section_no))
                    if name:
                        s.instructor = name

        # Request order, then catalog order within a wildcard. A course matched
        # by two patterns ('CSCI-UA 4xx' and 'CSCI-UA 472') is returned once.
        ordered: list[Course] = []
        seen: set[tuple[str, str]] = set()
        for pattern in patterns:
            for key in sorted(courses):
                if pattern.matches(key[0]) and key not in seen:
                    seen.add(key)
                    ordered.append(courses[key])
        return ordered