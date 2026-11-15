from models import Course, Section
import itertools



def valid_bundles(course: Course) -> list[list[Section]]:
    """Every legal way to register for a single course.

    A bundle is the set of sections you sign up for together. For a plain
    lecture course that is just [lecture]. For a course with tied sections it
    is the lecture plus **one of each component type** it offers, because a
    course can require several at once: EG-UY 1004 needs a lecture *and* a lab
    *and* a recitation, not a lecture plus one of them.

    Grouping by kind before taking the product is what makes that work. Treating
    every non-lecture as an interchangeable alternative produces bundles that
    are missing a required component and cannot actually be registered.

    A course with no lecture at all (CM-UY 1001: lab + recitation blocks, its
    lecture is the separate course CM-UY 1003) is anchored on `block_id`
    instead: each block contributes one of each component it offers. A
    lectureless section with no block cannot be placed and is left out, so a
    course made only of those yields no bundles -- see `unbundleable`.
    """
    bundles: list[list[Section]] = []

    for lecture in course.lectures():
        linked = course.recitations_for(lecture.section_id)

        if not linked:
            # No tied section, so the lecture by itself is a complete bundle.
            bundles.append([lecture])
            continue

        for combo in _one_of_each_kind(linked):
            bundles.append([lecture, *combo])

    if not course.lectures():
        blocks: dict[str, list[Section]] = {}
        for section in course.sections:
            if section.block_id is not None:
                blocks.setdefault(section.block_id, []).append(section)
        for block_id in sorted(blocks):
            bundles.extend(list(combo)
                           for combo in _one_of_each_kind(blocks[block_id]))

    return bundles


def _one_of_each_kind(sections: list[Section]):
    """Every way to pick exactly one section of each kind present."""
    by_kind: dict[int, list[Section]] = {}
    for section in sections:
        by_kind.setdefault(section.kind, []).append(section)

    # Sorted by kind so bundle order is deterministic run to run.
    options = [by_kind[kind] for kind in sorted(by_kind)]
    return itertools.product(*options)


def unbundleable(courses: list[Course]) -> list[str]:
    """Codes of courses with no registrable bundle.

    Any one of these makes every schedule impossible, since a schedule takes a
    bundle from each course. Without this check that surfaced as "0
    schedules" with no reason given -- the silent-default failure this
    project keeps paying for.
    """
    return [course.code for course in courses if not valid_bundles(course)]

def has_conflict(sections: list[Section]) -> bool:
    """True if any two meetings among these sections overlap in time."""
    meetings = [m for section in sections for m in section.meetings]
    for a, b in itertools.combinations(meetings, 2):
        if a.overlaps(b):
            return True
    return False


def generate_schedules(courses: list[Course],
                       bundle_cache: dict[int, list] | None = None) -> list[list[Section]]:
    """Every conflict-free schedule: one bundle from each course, with no
    two meetings overlapping. Each schedule is a flat list of sections.

    A depth-first search that abandons a branch at its first clash, instead of
    building every combination and checking it afterwards. Same results in the
    same order as the cartesian product it replaced -- it visits combinations
    in exactly the order `itertools.product` would -- but a conflict between
    the first two courses no longer costs every combination of the rest.
    That matters now that one request can schedule many course sets.

    `bundle_cache` (course id -> bundles) lets those sets share bundling work.
    """
    cache = bundle_cache if bundle_cache is not None else {}
    per_course: list[list[tuple[list[Section], list]]] = []
    for course in courses:
        if id(course) not in cache:
            # A bundle that clashes with itself (a lab over its own lecture)
            # can never appear, so drop it once here rather than per branch.
            cache[id(course)] = [b for b in valid_bundles(course)
                                 if not has_conflict(b)]
        per_course.append([(b, [m for s in b for m in s.meetings])
                           for b in cache[id(course)]])

    schedules: list[list[Section]] = []

    def extend(depth: int, sections: list[Section], meetings: list) -> None:
        if depth == len(per_course):
            schedules.append(sections)
            return
        for bundle, bundle_meetings in per_course[depth]:
            if any(a.overlaps(b) for a in bundle_meetings for b in meetings):
                continue
            extend(depth + 1, sections + bundle, meetings + bundle_meetings)

    extend(0, [], [])
    return schedules


def generate_for_sets(course_sets: list[tuple[Course, ...]]) -> list[list[Section]]:
    """Every conflict-free schedule across several course sets, in set order.

    Each set is scheduled on its own -- a schedule contains exactly the
    courses of one set -- and bundles are computed once per course however
    many sets include it.
    """
    cache: dict[int, list] = {}
    return [schedule for course_set in course_sets
            for schedule in generate_schedules(list(course_set), cache)]