"""Provider tests, driven by recorded class-search responses.

These run the real parsing and linkage code; only the HTTP fetch is skipped.
See conftest.provider for why the cache is pre-populated instead of mocking.
"""
import pytest

from models import Day, SectionKind
from nyu_provider import _block_stem, _to_minutes, _parse_meetings, KIND_MAP
from conftest import FALL_2026, SPRING_2027


# --- pure helpers ------------------------------------------------------------

def test_block_stem_parses_tandon_numbering():
    assert _block_stem("A1LB") == "A1"
    assert _block_stem("A1LC") == "A1"
    assert _block_stem("B12RC") == "B12"


def test_block_stem_is_none_for_cas_numbering():
    """CAS sections name no block, which is what selects the sequential
    strategy. Returning a stem here would silently switch strategies."""
    assert _block_stem("001") is None
    assert _block_stem("011a") is None
    assert _block_stem("") is None
    assert _block_stem(None) is None


def test_three_digit_start_times_are_padded():
    """The API returns '930' rather than '0930'; parsing without zfill would
    read it as 09:30 -> 93 minutes past midnight."""
    assert _to_minutes("930") == 9 * 60 + 30
    assert _to_minutes("1400") == 14 * 60


def test_meeting_times_are_a_json_string_nested_in_the_json():
    raw = '[{"meet_day": "0", "start_time": "930", "end_time": "1045"}]'
    meetings = _parse_meetings(raw)
    assert len(meetings) == 1
    assert meetings[0].day == Day.MON      # meet_day is 0-indexed from Monday
    assert meetings[0].start == 570


def test_no_meeting_times_yields_no_meetings():
    assert _parse_meetings("") == []


# --- linkage: the two strategies --------------------------------------------

def test_tandon_lab_links_to_its_own_block_not_the_previous_one(provider):
    """Regression. 'A1LB' sorts before 'A1LC' (same digits, so lexicographic
    order decides and LB < LC), so a sequential scan attached every lab to the
    PREVIOUS block's lecture and orphaned the first one. Invisible to the
    overlap check, because those blocks meet on different days."""
    course = provider.get_courses(FALL_2026, ["EG-UY 1004"])[0]
    by_id = {s.section_id: s for s in course.sections}

    labs = [s for s in course.sections if s.kind == SectionKind.LAB]
    assert labs, "fixture should contain labs"

    for lab in labs:
        assert lab.parent_section_id is not None, f"{lab.section_no} orphaned"
        parent = by_id[lab.parent_section_id]
        assert _block_stem(lab.section_no) == _block_stem(parent.section_no)
        assert parent.kind == SectionKind.LECTURE


def test_every_tandon_non_lecture_is_linked(provider):
    course = provider.get_courses(FALL_2026, ["EG-UY 1004"])[0]
    orphans = [s for s in course.sections
               if s.kind != SectionKind.LECTURE and not s.parent_section_id]
    assert orphans == []


def test_cas_recitation_links_to_the_lecture_that_starts_its_block(provider):
    course = provider.get_courses(FALL_2026, ["MATH-UA 121"])[0]
    by_id = {s.section_id: s for s in course.sections}

    for s in course.sections:
        if s.kind == SectionKind.LECTURE:
            continue
        parent = by_id[s.parent_section_id]
        assert parent.kind == SectionKind.LECTURE
        # Blocks run 001,006,011...: a recitation follows its own lecture.
        assert int(parent.section_no) < int(s.section_no)


def test_no_recitation_overlaps_its_own_lecture(provider):
    """The original linkage guard: a mislink usually shows up as a section
    colliding with the lecture it supposedly belongs to."""
    for code in ("MATH-UA 121", "CSCI-UA 102", "EG-UY 1004"):
        course = provider.get_courses(FALL_2026, [code])[0]
        by_id = {s.section_id: s for s in course.sections}
        for s in course.sections:
            if s.kind == SectionKind.LECTURE or not s.parent_section_id:
                continue
            lecture = by_id[s.parent_section_id]
            assert not any(a.overlaps(b) for a in s.meetings for b in lecture.meetings), \
                f"{code} {s.section_no} overlaps its lecture {lecture.section_no}"


# --- course assembly ---------------------------------------------------------

def test_sections_carry_the_fields_downstream_code_needs(provider):
    course = provider.get_courses(FALL_2026, ["MATH-UA 121"])[0]
    s = course.sections[0]
    assert s.section_id and s.section_no          # crn and join key
    assert s.course_code == "MATH-UA 121"
    assert s.kind in set(KIND_MAP.values())


def test_unknown_course_code_is_absent_rather_than_empty(provider):
    assert provider.get_courses(FALL_2026, ["MATH-UA 9999"]) == []


def test_requested_order_is_preserved(provider):
    codes = ["CSCI-UA 102", "MATH-UA 121", "CSCI-UA 101"]
    assert [c.code for c in provider.get_courses(FALL_2026, codes)] == codes


def test_brooklyn_sections_of_a_ua_course_are_tagged_brooklyn(provider):
    """The regression. MATH-UA looks like a Washington Square subject, but NYU
    runs sections 021 and 022 of MATH-UA 140 in Brooklyn. Inferring campus from
    the course code called them Manhattan and silently allowed impossible
    back-to-backs; the location filter gets it right."""
    from campuses import BROOKLYN, WASHINGTON_SQUARE

    course = provider.get_courses(FALL_2026, ["MATH-UA 140"])[0]
    by_no = {s.section_no: s for s in course.sections}

    assert by_no["021"].campus == BROOKLYN
    assert by_no["022"].campus == BROOKLYN
    assert by_no["001"].campus == WASHINGTON_SQUARE
    assert sum(1 for s in course.sections if s.campus == BROOKLYN) == 2


def test_campus_is_tagged_on_every_section(provider):
    course = provider.get_courses(FALL_2026, ["CSCI-UA 101"])[0]
    assert all(s.campus for s in course.sections)


def test_tandon_sections_are_brooklyn(provider):
    from campuses import BROOKLYN

    course = provider.get_courses(FALL_2026, ["EG-UY 1004"])[0]
    assert all(s.campus == BROOKLYN for s in course.sections)


def test_instructor_is_empty_from_the_api_alone(provider):
    """`instr` is always empty in NYU's public search; names come only from the
    department enrichment pass. Anything relying on this field would be dead."""
    course = provider.get_courses(FALL_2026, ["CSCI-UA 101"])[0]
    assert all(s.instructor is None for s in course.sections)


def test_enrichment_overlays_department_names(provider, offline_pages):
    from combined_instructors import CombinedInstructors
    from ma_instructors import MAInstructors
    from cs_instructors import CSInstructors

    provider._instructors = CombinedInstructors(MAInstructors(), CSInstructors())
    courses = provider.get_courses(FALL_2026, ["MATH-UA 121", "CSCI-UA 101"])

    named = [s for c in courses for s in c.sections
             if s.kind == SectionKind.LECTURE and s.instructor]
    assert named, "expected department names to be overlaid onto lectures"


def test_a_lone_lecture_adopts_every_section_whatever_the_numbering(provider):
    """Regression, found in the wild on CS-UY 2204. Its sections are 'ALAB',
    'BLAB', 'CLAB', 'DLAB' and 'LEC' -- no digits at all, so no block stem
    parses and the numeric sort ties them all at zero, ordering the four labs
    before the lecture. The sequential scan then saw a lab with no preceding
    lecture four times and dropped all of them from every bundle.

    With one lecture there is nothing to infer: a lab has no other lecture it
    could belong to."""
    course = provider.get_courses(FALL_2026, ["CS-UY 2204"])[0]
    lectures = [s for s in course.sections if s.kind == SectionKind.LECTURE]
    labs = [s for s in course.sections if s.kind == SectionKind.LAB]

    assert len(lectures) == 1, "fixture should have exactly one lecture"
    assert len(labs) == 4
    assert all(lab.parent_section_id == lectures[0].section_id for lab in labs)


def test_a_lone_lecture_still_bundles_with_each_lab(provider):
    """The consequence that actually mattered: orphaned labs vanish from every
    bundle, so the student is offered a lecture with no lab at all."""
    from scheduler import valid_bundles

    course = provider.get_courses(FALL_2026, ["CS-UY 2204"])[0]
    bundles = valid_bundles(course)
    assert len(bundles) == 4, "one bundle per lab"
    assert all(any(s.kind == SectionKind.LAB for s in b) for b in bundles)


def test_nyu_ca_bundle_adds_emsign_without_dropping_certifi_roots():
    """Regression from the deployed API. NYU's class search chains to emSign
    Root CA - G1, which certifi 2026.10.18 removed, so every request failed with
    CERTIFICATE_VERIFY_FAILED -- but only on fresh installs, never locally.
    The bundle must carry emSign *and* certifi's roots, so NYU moving to a
    mainstream CA does not break it again."""
    from pathlib import Path
    import certifi
    from nyu_provider import _nyu_ca_bundle

    bundle = Path(_nyu_ca_bundle()).read_text()
    assert 'Label: "emSign Root CA - G1"' in bundle
    assert Path(certifi.where()).read_text() in bundle


def test_a_lectureless_course_bundles_by_block(provider, capsys):
    """Regression, found in the wild on CM-UY 1001 (Spring 2027). The chem lab
    course has no lecture -- that is CM-UY 1003 -- only blocks of lab 'A1LB' +
    recitation 'A1RC'. Every section was dropped as an orphan, the course had
    no bundles, and any course list containing it produced 0 schedules with no
    reason given. The block is named in the section number, so each block is
    one bundle: its lab and its recitation."""
    from scheduler import valid_bundles

    course = provider.get_courses(SPRING_2027, ["CM-UY 1001"])[0]
    assert not course.lectures(), "fixture should have no lecture"
    assert "no lecture to link to" not in capsys.readouterr().out

    bundles = valid_bundles(course)
    assert len(bundles) == 7, "one bundle per block, A1 through D1"
    for bundle in bundles:
        assert sorted(s.kind for s in bundle) == [SectionKind.RECITATION,
                                                 SectionKind.LAB]
        assert {s.section_no[:2] for s in bundle} == {bundle[0].block_id}
