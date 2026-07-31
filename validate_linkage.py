# validate_linkage.py
from nyu_provider import NYUProvider, _block_stem
from models import SectionKind

FALL_2026 = "1268"
provider = NYUProvider()
courses = provider.get_courses(
    FALL_2026, ["MATH-UA 121", "CSCI-UA 102", "EG-UY 1004"]
)

for course in courses:
    by_id = {s.section_id: s for s in course.sections}
    problems = 0
    stem_mismatches = 0

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

        # Stem check. The overlap test above cannot catch a cross-block mislink
        # when the two blocks meet on different days -- exactly how every Tandon
        # lab got attached to the previous block's lecture. Where the section
        # number names its own block, that link is checkable directly.
        stem, parent_stem = _block_stem(s.section_no), _block_stem(lecture.section_no)
        if stem and parent_stem and stem != parent_stem:
            print(f"  MISLINK: {course.code} {s.section_no} belongs to block "
                  f"{stem} but is linked to {lecture.section_no} (block {parent_stem})")
            stem_mismatches += 1

    orphans = sum(1 for s in course.sections
                  if s.kind != SectionKind.LECTURE and not s.parent_section_id)
    kinds = {s.kind.name for s in course.sections if s.kind != SectionKind.LECTURE}
    print(f"{course.code}: {problems} self-conflicts, {stem_mismatches} block "
          f"mislinks, {orphans} orphan sections, components={sorted(kinds) or ['none']}")
