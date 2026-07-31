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
    """
    bundles: list[list[Section]] = []

    for lecture in course.lectures():
        linked = course.recitations_for(lecture.section_id)

        if not linked:
            # No tied section, so the lecture by itself is a complete bundle.
            bundles.append([lecture])
            continue

        by_kind: dict[int, list[Section]] = {}
        for section in linked:
            by_kind.setdefault(section.kind, []).append(section)

        # Sorted by kind so bundle order is deterministic run to run.
        options = [by_kind[kind] for kind in sorted(by_kind)]
        for combo in itertools.product(*options):
            bundles.append([lecture, *combo])

    return bundles

def has_conflict(sections: list[Section]) -> bool:
    """True if any two meetings among these sections overlap in time."""
    meetings = [m for section in sections for m in section.meetings]
    for a, b in itertools.combinations(meetings, 2):
        if a.overlaps(b):
            return True
    return False


def generate_schedules(courses: list[Course]) -> list[list[Section]]:
    """Every conflict-free schedule: one bundle from each course, with no
    two meetings overlapping. Each schedule is a flat list of sections."""
    per_course_bundles = [valid_bundles(course) for course in courses]

    schedules: list[list[Section]] = []
    for combo in itertools.product(*per_course_bundles):
        # combo is a tuple of bundles, one per course. Flatten to sections.
        sections = [section for bundle in combo for section in bundle]
        if not has_conflict(sections):
            schedules.append(sections)

    return schedules