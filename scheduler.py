from models import Course, Section
import itertools



def valid_bundles(course: Course) -> list[list[Section]]:
    """Every legal way to register for a single course.

    A bundle is the set of sections you sign up for together. For a plain
    lecture course that is just [lecture]. For a course with tied
    recitations, it is [lecture, recitation] for each recitation option.
    """
    bundles: list[list[Section]] = []

    for lecture in course.lectures():
        recitations = course.recitations_for(lecture.section_id)

        if not recitations:
            # No tied section, so the lecture by itself is a complete bundle.
            bundles.append([lecture])
        else:
            # One bundle per recitation choice for this lecture.
            for recitation in recitations:
                bundles.append([lecture, recitation])

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