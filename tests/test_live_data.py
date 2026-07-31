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

FALL_2026 = "1268"

# One course per numbering scheme in play: CAS numeric, and Tandon stem-based
# with three components.
LINKAGE_COURSES = ["MATH-UA 121", "CSCI-UA 102", "EG-UY 1004"]


@pytest.fixture(scope="module")
def live_courses():
    provider = NYUProvider()
    return provider.get_courses(FALL_2026, LINKAGE_COURSES)


def test_live_courses_all_resolve(live_courses):
    assert {c.code for c in live_courses} == set(LINKAGE_COURSES)


def test_no_live_section_is_orphaned(live_courses):
    for course in live_courses:
        orphans = [s.section_no for s in course.sections
                   if s.kind != SectionKind.LECTURE and not s.parent_section_id]
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


def test_live_multi_component_course_still_needs_every_component(live_courses):
    """EG-UY 1004 is the case that reportedly breaks other NYU schedulers."""
    from scheduler import valid_bundles

    course = next(c for c in live_courses if c.code == "EG-UY 1004")
    bundles = valid_bundles(course)
    assert bundles
    assert any(len({s.kind for s in b}) == 3 for b in bundles), \
        "expected at least one lecture+lab+recitation bundle"


def test_live_instructor_joins_still_match(live_courses):
    """Guards both department joins at once. A parsing drift on either page
    shows up here as a collapse in match rate, not an exception."""
    provider = NYUProvider(
        instructors=CombinedInstructors(MAInstructors(), CSInstructors()))
    courses = provider.get_courses(
        FALL_2026, ["MATH-UA 121", "MATH-UA 122", "CSCI-UA 101", "CSCI-UA 102"])

    for course in courses:
        lectures = [s for s in course.sections if s.kind == SectionKind.LECTURE]
        named = [s for s in lectures if s.instructor]
        # Not 100%: a department may legitimately still list a section as TBA.
        assert len(named) >= len(lectures) - 1, (
            f"{course.code}: only {len(named)} of {len(lectures)} lectures "
            f"matched an instructor")


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
    from ratings import RMPRatings

    provider = RMPRatings(delay=0, cache_path=None, verbose=False)
    ratings = provider.ratings_for({"Evan Korth": "Computer Science"})

    assert "Evan Korth" in ratings
    assert 0.0 <= ratings["Evan Korth"] <= 5.0


def test_live_api_still_omits_instructors():
    """`instr` has always been empty. If NYU ever populates it, the department
    scrapers become redundant -- worth knowing immediately."""
    provider = NYUProvider()
    course = provider.get_courses(FALL_2026, ["CSCI-UA 101"])[0]
    assert all(s.instructor is None for s in course.sections)
