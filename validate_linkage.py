# validate_linkage.py
from nyu_provider import NYUProvider
from models import SectionKind

FALL_2026 = "1268"
provider = NYUProvider()
courses = provider.get_courses(FALL_2026, ["MATH-UA 121", "CSCI-UA 102"])

for course in courses:
    by_id = {s.section_id: s for s in course.sections}
    problems = 0
    for s in course.sections:
        if s.kind == SectionKind.LECTURE or not s.parent_section_id:
            continue
        lecture = by_id.get(s.parent_section_id)
        if not lecture:
            continue
        if any(a.overlaps(b) for a in s.meetings for b in lecture.meetings):
            print(f"  CONFLICT: {course.code} rec {s.section_id} overlaps "
                  f"its lecture {lecture.section_id}")
            problems += 1
    orphans = sum(1 for s in course.sections
                  if s.kind != SectionKind.LECTURE and not s.parent_section_id)
    print(f"{course.code}: {problems} self-conflicts, {orphans} orphan sections")