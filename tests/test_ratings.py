"""Rating lookup, matching, and shrinkage.

The matching cases here are all real: every candidate list is a recorded RMP
response, including the ones that make matching hard -- a shared surname, a
middle name NYU publishes and RMP does not, a professor absent from RMP, and
junk profiles carrying avgRating 0.0 with zero reviews.
"""
import json
from pathlib import Path

import pytest

from campuses import BROOKLYN, WASHINGTON_SQUARE
from ratings import (
    RMPRatings, NullRatings, InstructorHint, shrink, department_hint,
    _first_last, _norm, NYU_SCHOOL_ID, TANDON_SCHOOL_ID,
    PRIOR_MEAN, PRIOR_WEIGHT,
)

FIXTURES = Path(__file__).parent / "fixtures"
TEACHERS = json.loads((FIXTURES / "rmp_teachers.json").read_text())
# Recorded from both directories, because RMP files Tandon separately:
# "constantine" returns two unrelated people at main NYU and Adam
# Constantine only in Tandon.
BY_SCHOOL = json.loads((FIXTURES / "rmp_schools.json").read_text())


@pytest.fixture
def rmp(monkeypatch):
    """An RMPRatings whose search is served from recorded responses."""
    provider = RMPRatings(delay=0, cache_path=None, verbose=False)

    def fake_search(text, school=None):
        key = _norm(text)
        if school == TANDON_SCHOOL_ID:
            return BY_SCHOOL["tandon"].get(key, [])
        return TEACHERS.get(key) or BY_SCHOOL["main"].get(key, [])

    provider.searched = []
    def recording(text, school=None):
        provider.searched.append((_norm(text), school))
        return fake_search(text, school)

    monkeypatch.setattr(provider, "_search", recording)
    return provider


# --- name handling -----------------------------------------------------------

def test_middle_names_are_dropped():
    """NYU publishes 'Hilbert Gene Locklear'; RMP has 'Hilbert Locklear'.
    Matching on the full string would miss him entirely."""
    assert _first_last("Hilbert Gene Locklear") == ("hilbert", "locklear")


def test_name_normalization_absorbs_rmp_whitespace():
    """RMP's own records carry doubled and trailing spaces inside firstName."""
    assert _norm("Tobias  ") == "tobias"
    assert _first_last("  Evan   Korth ") == ("evan", "korth")


def test_single_token_name_has_no_first_name():
    assert _first_last("Cher") == ("", "cher")


def test_department_hint_maps_subject_to_rmp_department():
    assert department_hint("CSCI-UA 102") == "Computer Science"
    assert department_hint("MATH-UA 121") == "Mathematics"
    assert department_hint("EXPOS-UA 1") is None


# --- shrinkage ---------------------------------------------------------------

def test_shrinkage_pulls_thin_evidence_toward_the_prior():
    """A single 5-star review must not outrank an established 4.3."""
    one_review = shrink(5.0, 1)
    established = shrink(4.3, 100)
    assert one_review < established
    assert one_review == pytest.approx((5.0 + PRIOR_WEIGHT * PRIOR_MEAN) / (1 + PRIOR_WEIGHT))


def test_shrinkage_barely_moves_well_reviewed_ratings():
    assert shrink(4.3, 500) == pytest.approx(4.3, abs=0.05)


def test_shrinkage_of_a_prior_valued_rating_is_a_fixed_point():
    assert shrink(PRIOR_MEAN, 7) == pytest.approx(PRIOR_MEAN)


def test_shrinkage_stays_inside_the_rating_scale():
    for average, count in [(0.0, 1), (5.0, 1), (0.0, 999), (5.0, 999)]:
        assert 0.0 <= shrink(average, count) <= 5.0


# --- matching ----------------------------------------------------------------

def test_matches_an_unambiguous_professor(rmp):
    ratings = rmp.ratings_for({"Evan Korth": InstructorHint("Computer Science")})
    assert ratings["Evan Korth"] == pytest.approx(shrink(3.5, 115))


def test_shared_surname_is_resolved_by_first_name(rmp):
    """'Foster' returns Joseph (Mathematics), Mina (Sociology), and a junk
    'foster provost' entry. Only the first name disambiguates them."""
    ratings = rmp.ratings_for({"Joseph Foster": InstructorHint("Mathematics")})
    assert ratings["Joseph Foster"] == pytest.approx(shrink(2.8, 144))


def test_professor_absent_from_rmp_is_left_unrated(rmp):
    """Absent means neutral, not zero: _score_professor ignores names it has no
    rating for, which is the correct 'no information' behavior."""
    assert rmp.ratings_for({"Federico Sichetti": InstructorHint("Computer Science")}) == {}


def test_wrong_first_name_does_not_match_a_surname_sibling(rmp):
    """Guards against quietly attaching Michael Shum's 4.9 to Fanny Shum."""
    assert rmp.ratings_for({"Imaginary Shum": InstructorHint("Mathematics")}) == {}


def test_middle_name_still_matches_the_right_person(rmp):
    ratings = rmp.ratings_for({"Hilbert Gene Locklear": InstructorHint("Computer Science")})
    assert ratings["Hilbert Gene Locklear"] == pytest.approx(shrink(2.9, 70))


def test_profile_with_no_reviews_is_not_treated_as_a_zero_rating(rmp):
    """RMP reports avgRating 0.0 for unreviewed profiles. Scoring that as a
    real 0.0 would rank an unreviewed professor as the worst possible."""
    unreviewed = [{"firstName": "Mina", "lastName": "Foster",
                   "department": "Sociology", "avgRating": 0, "numRatings": 0}]
    match, reason = rmp._match("Mina Foster", "Sociology", unreviewed)
    assert match is None
    assert "no ratings" in reason


def test_department_hint_breaks_a_genuine_tie(rmp):
    same_name = [
        {"firstName": "Alex", "lastName": "Rivera", "department": "Mathematics",
         "avgRating": 4.0, "numRatings": 20},
        {"firstName": "Alex", "lastName": "Rivera", "department": "History",
         "avgRating": 2.0, "numRatings": 20},
    ]
    match, reason = rmp._match("Alex Rivera", "Mathematics", same_name)
    assert reason is None
    assert match["department"] == "Mathematics"


def test_unresolvable_tie_is_refused_rather_than_guessed(rmp):
    """Two same-name, same-department professors. A wrong rating is worse than
    no rating, so this returns nothing instead of picking one."""
    same_name = [
        {"firstName": "Alex", "lastName": "Rivera", "department": "Mathematics",
         "avgRating": 4.0, "numRatings": 20},
        {"firstName": "Alex", "lastName": "Rivera", "department": "Mathematics",
         "avgRating": 2.0, "numRatings": 20},
    ]
    match, reason = rmp._match("Alex Rivera", "Mathematics", same_name)
    assert match is None
    assert "ambiguous" in reason


def test_missing_hint_does_not_force_a_guess(rmp):
    same_name = [
        {"firstName": "Alex", "lastName": "Rivera", "department": "Mathematics",
         "avgRating": 4.0, "numRatings": 20},
        {"firstName": "Alex", "lastName": "Rivera", "department": "History",
         "avgRating": 2.0, "numRatings": 20},
    ]
    assert rmp._match("Alex Rivera", None, same_name)[0] is None


def test_lookup_failure_degrades_to_unrated(monkeypatch):
    """A network problem should cost the professor factor, not the whole run."""
    import requests

    provider = RMPRatings(delay=0, cache_path=None, verbose=False)

    def boom(text, school=None):
        raise requests.RequestException("connection reset")

    monkeypatch.setattr(provider, "_search", boom)
    assert provider.ratings_for({"Evan Korth": InstructorHint("Computer Science")}) == {}


def test_empty_names_are_skipped(rmp):
    assert rmp.ratings_for({"": InstructorHint()}) == {}


# --- the interface -----------------------------------------------------------

def test_null_provider_returns_nothing():
    assert NullRatings().ratings_for({"Evan Korth": InstructorHint("Computer Science")}) == {}


def test_ratings_land_on_the_zero_to_five_scale_scoring_expects(rmp):
    """_score_professor divides by 5.0, so anything outside 0..5 would push a
    normalized factor outside 0..1 and corrupt every weighted total."""
    ratings = rmp.ratings_for({
        "Evan Korth": InstructorHint("Computer Science"),
        "Joseph Foster": InstructorHint("Mathematics"),
        "Fanny Shum": InstructorHint("Mathematics"),
    })
    assert ratings
    assert all(0.0 <= value <= 5.0 for value in ratings.values())


def test_ratings_feed_scoring_without_scoring_touching_the_network(rmp):
    """The integration contract: ratings are resolved up front and handed to
    Preferences as plain data, so scoring stays pure and deterministic."""
    from models import Day, Meeting, Section, SectionKind, hhmm
    from scoring import Preferences, _score_professor, PROFESSOR

    ratings = rmp.ratings_for({"Evan Korth": InstructorHint("Computer Science")})
    lecture = Section("L", "CSCI-UA 102", SectionKind.LECTURE, "Evan Korth",
                      [Meeting(Day.MON, hhmm("12:00"), hhmm("13:00"))])
    prefs = Preferences(preferred_days={Day.MON}, time_start=0, time_end=1439,
                        professor_ratings=ratings, weights={PROFESSOR: 1})

    assert _score_professor([lecture], prefs) == pytest.approx(shrink(3.5, 115) / 5.0)


# --- NYU is more than one school on RMP --------------------------------------
#
# Tandon is filed as its own school with its own faculty, so a Brooklyn
# professor is simply absent from the main NYU directory. Three instructors on
# a real course list (Constantine, Malcom, Qian, all teaching MA-UY) came back
# "no name match" against main NYU and all three sit in Tandon with ratings.

def test_a_brooklyn_professor_is_found_in_the_tandon_directory(rmp):
    """The regression. Adam Constantine is not in main NYU's directory at all;
    searching only there reported him unrated forever."""
    ratings = rmp.ratings_for({
        "Adam Constantine": InstructorHint("Mathematics", BROOKLYN)})
    assert "Adam Constantine" in ratings


def test_campus_decides_which_directory_is_searched_first(rmp):
    rmp.ratings_for({"Adam Constantine": InstructorHint("Mathematics", BROOKLYN)})
    assert rmp.searched[0][1] == TANDON_SCHOOL_ID

    rmp.searched.clear()
    rmp.ratings_for({"Evan Korth": InstructorHint("Computer Science", WASHINGTON_SQUARE)})
    assert rmp.searched[0][1] == NYU_SCHOOL_ID


def test_the_other_directory_is_a_fallback_not_a_replacement(rmp):
    """A Washington Square professor absent from main NYU is still worth
    looking for in Tandon -- cross-campus teaching happens."""
    rmp.ratings_for({
        "Adam Constantine": InstructorHint("Mathematics", WASHINGTON_SQUARE)})
    assert [s for _, s in rmp.searched] == [NYU_SCHOOL_ID, TANDON_SCHOOL_ID]


def test_an_unrated_professor_does_not_trigger_a_second_directory(rmp):
    """Jinkun Geng IS in the main directory, with zero reviews. That is an
    answer about the right person. Searching Tandon after it could only find
    somebody else who happens to share the name."""
    rmp.ratings_for({"Jinkun Geng": InstructorHint("Computer Science",
                                                   WASHINGTON_SQUARE)})
    assert [s for _, s in rmp.searched] == [NYU_SCHOOL_ID]


def test_no_campus_still_searches_both(rmp):
    """Campus only sets the order; a missing one must not disable the fallback."""
    ratings = rmp.ratings_for({"Adam Constantine": InstructorHint("Mathematics")})
    assert "Adam Constantine" in ratings


def test_the_search_cache_is_keyed_by_school(monkeypatch):
    """Without the school in the key, the second directory is served the first
    one's answer and the fallback silently never happens."""
    provider = RMPRatings(delay=0, cache_path=None, verbose=False)
    calls = []

    class FakeResponse:
        def __init__(self, nodes):
            self._nodes = nodes

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"newSearch": {"teachers": {
                "edges": [{"node": n} for n in self._nodes]}}}}

    def fake_post(url, **kwargs):
        school = kwargs["json"]["variables"]["school"]
        calls.append(school)
        return FakeResponse(BY_SCHOOL["tandon" if school == TANDON_SCHOOL_ID
                                      else "main"]["constantine"])

    monkeypatch.setattr("ratings.requests.post", fake_post)

    main = provider._search("constantine", NYU_SCHOOL_ID)
    tandon = provider._search("constantine", TANDON_SCHOOL_ID)
    assert calls == [NYU_SCHOOL_ID, TANDON_SCHOOL_ID], "second school must be fetched"
    assert main != tandon

    provider._search("constantine", NYU_SCHOOL_ID)   # now served from cache
    assert len(calls) == 2
