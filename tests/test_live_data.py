"""Live checks against NYU's real endpoints. Run deliberately: pytest -m live

These replace validate_linkage.py and validate_join.py. The offline tests pin
our parsing against recorded data; these catch the other failure mode, where
NYU's live data changes shape underneath us -- a new department numbering its
sections differently, or a department page reorganizing its HTML.

Both scripts previously printed their findings, which is why the Tandon linkage
bug survived: validate_linkage.py reported "0 self-conflicts" while every lab
was attached to the wrong lecture. Assertions fail; prints do not.
"""
import pytest

from models import SectionKind
from nyu_provider import NYUProvider, _block_stem
from ma_instructors import MAInstructors
from cs_instructors import CSInstructors
from combined_instructors import CombinedInstructors

pytestmark = pytest.mark.live

SPRING_2027 = "1274"

# One course per numbering scheme in play: CAS numeric, Tandon stem-based with
# three components, Tandon lettered (letters name lectures), and Tandon
# course-wide labs ('LAB1' names no lecture).
LINKAGE_COURSES = ["MATH-UA 121", "CSCI-UA 102", "EG-UY 1004",
                   "MA-UY 1044", "CS-UY 2124"]


@pytest.fixture(scope="module")
def live_courses():
    provider = NYUProvider()
    return provider.get_courses(SPRING_2027, LINKAGE_COURSES)


def test_live_courses_all_resolve(live_courses):
    assert {c.code for c in live_courses} == set(LINKAGE_COURSES)


def test_no_live_section_is_orphaned(live_courses):
    for course in live_courses:
        orphans = [s.section_no for s in course.sections
                   if s.kind != SectionKind.LECTURE and not s.parent_section_id
                   and not s.any_lecture]
        assert orphans == [], f"{course.code} has unlinked sections: {orphans}"


def test_no_live_section_overlaps_its_own_lecture(live_courses):
    """A mislink usually surfaces as a section colliding with the lecture it
    supposedly belongs to. Blind to blocks that meet on different days, which
    is why the stem check below exists too."""
    for course in live_courses:
        by_id = {s.section_id: s for s in course.sections}
        for s in course.sections:
            if s.kind == SectionKind.LECTURE or not s.parent_section_id:
                continue
            lecture = by_id[s.parent_section_id]
            assert not any(a.overlaps(b) for a in s.meetings for b in lecture.meetings), \
                f"{course.code} {s.section_no} overlaps lecture {lecture.section_no}"


def test_live_stem_numbered_sections_stay_within_their_block(live_courses):
    """Catches cross-block mislinks regardless of meeting times, wherever the
    section number names its own block."""
    for course in live_courses:
        by_id = {s.section_id: s for s in course.sections}
        for s in course.sections:
            if s.kind == SectionKind.LECTURE or not s.parent_section_id:
                continue
            stem = _block_stem(s.section_no)
            parent_stem = _block_stem(by_id[s.parent_section_id].section_no)
            if stem and parent_stem:
                assert stem == parent_stem, (
                    f"{course.code} {s.section_no} (block {stem}) linked to "
                    f"block {parent_stem}")


def test_no_live_lab_course_offers_a_lecture_without_its_lab(live_courses):
    """The failure the user hit on CS-UY 2124: lectures bundled with no lab.
    A lecture-only bundle is legitimate only when nothing can pair with it."""
    from scheduler import valid_bundles

    for course in live_courses:
        for bundle in valid_bundles(course):
            if len(bundle) == 1:
                lecture = bundle[0]
                assert not course.recitations_for(lecture.section_id), (
                    f"{course.code} {lecture.section_no} bundled alone despite "
                    f"having components")
            kinds = {s.kind for s in bundle}
            needed = {s.kind for s in course.recitations_for(bundle[0].section_id)}
            assert needed <= kinds, f"{course.code} bundle missing {needed - kinds}"


def test_live_multi_component_course_still_needs_every_component(live_courses):
    """EG-UY 1004 is the case that reportedly breaks other NYU schedulers."""
    from scheduler import valid_bundles

    course = next(c for c in live_courses if c.code == "EG-UY 1004")
    bundles = valid_bundles(course)
    assert bundles
    assert any(len({s.kind for s in b}) == 3 for b in bundles), \
        "expected at least one lecture+lab+recitation bundle"


def test_live_instructor_joins_still_match(live_courses):
    """Guards both department joins at once: every section a department
    *names* must land on a real lecture from the class search.

    It deliberately does not demand that most lectures be named. That measured
    how far a department had got with staffing, not the join: for Spring 2027
    the CS page still read 'STAFF' for 4 of CSCI-UA 101's 10 lectures, and
    Courant had published no MATH-UA at all, while every name that did exist
    joined correctly. A parsing drift shows up here as names that fail to land,
    and a page that stops parsing entirely as no names at all.
    """
    instructors = CombinedInstructors(MAInstructors(), CSInstructors())
    provider = NYUProvider(instructors=instructors)
    codes = ["MATH-UA 121", "MATH-UA 122", "CSCI-UA 101", "CSCI-UA 102"]
    courses = {c.code: c for c in provider.get_courses(SPRING_2027, codes)}
    lookup = instructors.instructors_for(SPRING_2027)

    named = {(code, sec): name for (code, sec), name in lookup.items()
             if code in courses}
    assert named, "no department named anyone for these courses: likely a parsing break"

    for (code, sec), name in named.items():
        lecture = next((s for s in courses[code].sections if s.section_no == sec), None)
        assert lecture is not None, f"{code} {sec} ({name}) matches no API section"
        assert lecture.instructor == name, f"{code} {sec}: name not overlaid"


def test_live_rmp_schema_still_returns_the_fields_we_read():
    """The endpoint is unofficial and unversioned. If RMP renames a field or
    changes the search shape, every rating silently vanishes and the professor
    factor goes quietly inert again -- exactly the state this work fixed."""
    from ratings import RMPRatings

    provider = RMPRatings(delay=0, cache_path=None, verbose=False)
    candidates = provider._search("Korth")

    assert candidates, "expected at least one candidate for a known NYU professor"
    node = candidates[0]
    for field in ("firstName", "lastName", "department", "avgRating", "numRatings"):
        assert field in node, f"RMP response is missing {field}"


def test_live_known_professor_still_matches_and_scores():
    from campuses import WASHINGTON_SQUARE
    from ratings import RMPRatings, InstructorHint

    provider = RMPRatings(delay=0, cache_path=None, verbose=False)
    ratings = provider.ratings_for({
        "Evan Korth": InstructorHint("Computer Science", WASHINGTON_SQUARE)})

    assert "Evan Korth" in ratings
    assert 0.0 <= ratings["Evan Korth"] <= 5.0


def test_live_tandon_is_a_separate_directory_with_its_own_faculty():
    """The pinned Tandon schoolID is the whole basis for finding Brooklyn
    professors. If RMP merges the schools or reissues the id, every MA-UY and
    CS-UY instructor silently goes unrated again -- worth failing loudly.

    Adam Constantine is the recorded case: absent from the main NYU directory,
    present in Tandon.
    """
    from campuses import BROOKLYN
    from ratings import (RMPRatings, InstructorHint, NYU_SCHOOL_ID,
                         TANDON_SCHOOL_ID, _norm)

    provider = RMPRatings(delay=0, cache_path=None, verbose=False)

    main = provider._search("Constantine", NYU_SCHOOL_ID)
    tandon = provider._search("Constantine", TANDON_SCHOOL_ID)
    assert not any(_norm(t["firstName"]) == "adam" for t in main), \
        "Constantine appearing in the main directory means the split has changed"
    assert any(_norm(t["firstName"]) == "adam" for t in tandon), \
        "Adam Constantine missing from Tandon: schoolID may have been reissued"

    ratings = provider.ratings_for({
        "Adam Constantine": InstructorHint("Mathematics", BROOKLYN)})
    assert "Adam Constantine" in ratings


def test_live_api_still_omits_instructors():
    """`instr` has always been empty. If NYU ever populates it, the department
    scrapers become redundant -- worth knowing immediately."""
    provider = NYUProvider()
    course = provider.get_courses(SPRING_2027, ["CSCI-UA 101"])[0]
    assert all(s.instructor is None for s in course.sections)
