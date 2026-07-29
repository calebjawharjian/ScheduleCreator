from models import Course, Section, SectionKind, Meeting, Day, hhmm


def build_fixture_courses() -> list[Course]:
    calc = Course(
        code="MATH-UA 121",
        title="Calculus I",
        sections=[
            Section("MATH-UA-121-001", "MATH-UA 121", SectionKind.LECTURE, "Alice Nguyen",
                    [Meeting(Day.MON, hhmm("11:00"), hhmm("12:15")),
                     Meeting(Day.WED, hhmm("11:00"), hhmm("12:15"))]),
            Section("MATH-UA-121-R01", "MATH-UA 121", SectionKind.RECITATION, "Staff",
                    [Meeting(Day.FRI, hhmm("11:00"), hhmm("12:15"))],
                    parent_section_id="MATH-UA-121-001"),
            Section("MATH-UA-121-R02", "MATH-UA 121", SectionKind.RECITATION, "Staff",
                    [Meeting(Day.FRI, hhmm("14:00"), hhmm("15:15"))],
                    parent_section_id="MATH-UA-121-001"),

            Section("MATH-UA-121-002", "MATH-UA 121", SectionKind.LECTURE, "Bob Martinez",
                    [Meeting(Day.TUE, hhmm("09:30"), hhmm("10:45")),
                     Meeting(Day.THU, hhmm("09:30"), hhmm("10:45"))]),
            Section("MATH-UA-121-R03", "MATH-UA 121", SectionKind.RECITATION, "Staff",
                    [Meeting(Day.TUE, hhmm("12:30"), hhmm("13:45"))],
                    parent_section_id="MATH-UA-121-002"),
        ],
    )

    data_structures = Course(
        code="CSCI-UA 102",
        title="Data Structures",
        sections=[
            Section("CSCI-UA-102-001", "CSCI-UA 102", SectionKind.LECTURE, "Carol Smith",
                    [Meeting(Day.MON, hhmm("12:30"), hhmm("13:45")),
                     Meeting(Day.WED, hhmm("12:30"), hhmm("13:45"))]),
            # This one overlaps MATH-UA-121-002 on Tue/Thu, so the scheduler
            # must reject the (121-002 + 102-002) combination.
            Section("CSCI-UA-102-002", "CSCI-UA 102", SectionKind.LECTURE, "Dan Brown",
                    [Meeting(Day.TUE, hhmm("10:00"), hhmm("11:15")),
                     Meeting(Day.THU, hhmm("10:00"), hhmm("11:15"))]),
        ],
    )

    return [calc, data_structures]