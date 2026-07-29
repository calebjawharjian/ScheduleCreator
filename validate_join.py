# validate_join.py
from nyu_provider import NYUProvider
from courant import CourantInstructors
from models import SectionKind

provider = NYUProvider(instructors=CourantInstructors())
courses = provider.get_courses("1268", ["MATH-UA 121", "MATH-UA 122", "CSCI-UA 102"])

for course in courses:
    lectures = [s for s in course.sections if s.kind == SectionKind.LECTURE]
    named = [s for s in lectures if s.instructor]
    print(f"{course.code}: {len(named)} of {len(lectures)} lectures matched")
    for s in lectures:
        print(f"   sec {s.section_no}  {s.instructor or '(unmatched)'}")