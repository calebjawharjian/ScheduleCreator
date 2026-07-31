"""Campus, taken from NYU's own location filter rather than inferred.

An earlier version of this module guessed campus from the school code (`UY`/`GY`
= Brooklyn, everything else Washington Square). **That was wrong**, and wrong in
the dangerous direction: it silently permitted impossible schedules. NYU runs
some Washington Square-coded courses in Brooklyn -- 9 MATH-UA sections in Fall
2026, including MATH-UA 140 section 021 and MATH-UA 251 section 007. The class
search itself knows better, so ask it.

Campus is a property of the **section**, not the course. MATH-UA 140 has 32
sections: 021 and 022 meet in Brooklyn, the other 30 at Washington Square.
Anything keyed by course code cannot represent that.
"""
from __future__ import annotations

WASHINGTON_SQUARE = "Washington Square"
BROOKLYN = "Brooklyn (Tandon)"

# Values from the class search's own Location dropdown (`select#crit-camp`),
# passed as a `camp` criterion.
#
# Only Brooklyn is queried. The Washington Square option is NOT the complement:
# its `WS*` wildcard also matches `WS@BRKLN`, so it returns Brooklyn sections
# too -- for MATH-UA it returned all 309 rows including the 9 Brooklyn ones.
# Querying Brooklyn and treating everything else as Washington Square is the
# only reading of this filter that partitions correctly.
BROOKLYN_CAMP_CODES = "WS@BRKLN,WS@INDUS"

# Kept for reference; deliberately unused. See the note above before reaching
# for it as a complement to the Brooklyn query.
WASHINGTON_SQUARE_CAMP_CODES = (
    "AD@GLOBAL-WS,AD@WS,SH@GLOBAL-WS,WS*,WS@2BRD,WS@JD,WS@MT,"
    "WS@OC,WS@PU,WS@WS,WS@WW,AD@GLOBAL-WS"
)


def campuses_in(courses) -> set[str]:
    """Every campus represented across these courses' sections.

    Reads section tags, so a Manhattan course with one Brooklyn section counts
    as spanning both -- which is exactly the case that motivated this.
    """
    return {section.campus
            for course in courses
            for section in course.sections
            if section.campus}
