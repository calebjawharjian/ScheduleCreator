"""Scraper tests against recorded department HTML.

Both joins are exact on (course_code, section_no), so a single character of
parsing drift -- an unstripped zero-width space, a kept mobile label -- silently
produces zero matches rather than an error. These pin the parsing, not the data.
"""
import pytest

from conftest import FALL_2026
from cs_instructors import CSInstructors, NON_NAMES
from ma_instructors import MAInstructors
from combined_instructors import CombinedInstructors


@pytest.fixture
def cs(offline_pages):
    return CSInstructors().instructors_for(FALL_2026)


@pytest.fixture
def ma(offline_pages):
    return MAInstructors().instructors_for(FALL_2026)


# --- CS department -----------------------------------------------------------

def test_cs_section_numbers_have_no_zero_width_space(cs):
    """The catalog anchor embeds a literal U+200B before the section number.
    It is invisible and regex \\s does not match it, so an unstripped one
    yields keys like ('CSCI-UA 101', '\\u200b001') that join to nothing."""
    assert cs, "expected parsed CS entries"
    for _, section_no in cs:
        assert "​" not in section_no
        assert section_no.strip() == section_no


def test_cs_catalog_number_padding_is_dropped(cs):
    """'CSCI-UA.0101-001' must become 'CSCI-UA 101' to match the API's code."""
    codes = {code for code, _ in cs}
    assert "CSCI-UA 101" in codes
    assert not any(code.split()[-1].startswith("0") for code in codes)


def test_cs_placeholders_are_not_treated_as_instructors(cs):
    """TBA, STAFF and CANCELLED are states, not people. Joining them in would
    give every such section a shared fake 'instructor' that scores together."""
    names = {name.upper() for name in cs.values()}
    assert names.isdisjoint(NON_NAMES)


def test_cs_placeholder_rows_are_present_in_the_fixture(offline_pages):
    """Guards the test above from passing vacuously if the recorded page ever
    loses its placeholder rows."""
    from pathlib import Path
    html = (Path(__file__).parent / "fixtures" / "cs_schedule_fall_2026.html").read_text()
    for token in ("TBA", "STAFF", "CANCELLED"):
        assert token in html


def test_cs_names_are_extracted_from_anchor_wrapped_cells(cs):
    """Some instructors are plain text, others are links to a homepage."""
    assert any(" " in name for name in cs.values())


# --- Math department (Courant) ----------------------------------------------

def test_ma_finds_the_expected_lecture_sections(ma):
    """Courant lists only primary lectures, which is what the professor factor
    scores. MATH-UA 121 Fall 2026 runs blocks at 001/006/011/016/021/026/031."""
    sections = {no for code, no in ma if code == "MATH-UA 121"}
    assert {"001", "006", "011"} <= sections


def test_ma_strips_the_mobile_header_labels(ma):
    """Each cell repeats its own header inside span.header-text for mobile.
    Left in place, every name would read 'Instructor(s): Fanny Shum'."""
    for name in ma.values():
        assert "Instructor" not in name
        assert ":" not in name


def test_ma_catalog_number_padding_is_dropped(ma):
    assert any(code == "MATH-UA 121" for code, _ in ma)


def test_ma_cross_listed_row_takes_the_first_code(ma):
    """Rows like 'MATH-GA.2010-001 CSCI-GA 2420.001' hold two codes in one
    cell; the regex uses search(), so the first wins."""
    codes = {code for code, _ in ma}
    assert not any(code.startswith("CSCI-GA") for code in codes)


def test_both_sources_produce_the_same_key_shape(cs, ma):
    """Interchangeability is what lets CombinedInstructors merge them and lets
    NYUProvider stay ignorant of which department a name came from."""
    for lookup in (cs, ma):
        for key in lookup:
            assert isinstance(key, tuple) and len(key) == 2
            assert all(isinstance(part, str) for part in key)


# --- merging -----------------------------------------------------------------

def test_combined_lookup_contains_both_departments(offline_pages):
    combined = CombinedInstructors(MAInstructors(), CSInstructors())
    merged = combined.instructors_for(FALL_2026)
    subjects = {code.split()[0] for code, _ in merged}
    assert {"MATH-UA", "CSCI-UA"} <= subjects


def test_combined_lookup_is_the_union_of_its_sources(offline_pages):
    ma_only = MAInstructors().instructors_for(FALL_2026)
    cs_only = CSInstructors().instructors_for(FALL_2026)
    merged = CombinedInstructors(MAInstructors(), CSInstructors()).instructors_for(FALL_2026)
    assert len(merged) == len({**ma_only, **cs_only})


def test_unknown_term_returns_empty_rather_than_raising(offline_pages):
    """A term with no published semester slug should degrade to 'no names',
    not take down a run that would otherwise work."""
    assert CSInstructors().instructors_for("9999") == {}
    assert MAInstructors().instructors_for("9999") == {}
