from models import Course, Day, Meeting, Section, SectionKind, hhmm
from scheduler import valid_bundles, has_conflict, generate_schedules


def m(day, start, end):
    return Meeting(day, hhmm(start), hhmm(end))


def section(sid, kind, meetings=(), parent=None):
    return Section(sid, "X 1", kind, None, list(meetings), parent_section_id=parent)


def test_lecture_only_course_yields_one_bundle():
    lec = section("L1", SectionKind.LECTURE)
    assert valid_bundles(Course("X 1", "", [lec])) == [[lec]]


def test_one_bundle_per_recitation_choice():
    lec = section("L1", SectionKind.LECTURE)
    r1 = section("R1", SectionKind.RECITATION, parent="L1")
    r2 = section("R2", SectionKind.RECITATION, parent="L1")
    bundles = valid_bundles(Course("X 1", "", [lec, r1, r2]))
    assert bundles == [[lec, r1], [lec, r2]]


def test_lecture_requiring_lab_and_recitation_takes_one_of_each():
    """Regression: EG-UY 1004 needs lecture AND lab AND recitation. Treating
    non-lectures as interchangeable alternatives produced bundles missing a
    required component -- schedules that cannot actually be registered."""
    lec = section("L1", SectionKind.LECTURE)
    lab1 = section("B1", SectionKind.LAB, parent="L1")
    lab2 = section("B2", SectionKind.LAB, parent="L1")
    rec = section("R1", SectionKind.RECITATION, parent="L1")

    bundles = valid_bundles(Course("X 1", "", [lec, lab1, lab2, rec]))

    assert len(bundles) == 2
    for bundle in bundles:
        kinds = sorted(s.kind for s in bundle)
        assert kinds == [SectionKind.LECTURE, SectionKind.RECITATION, SectionKind.LAB]


def test_bundles_cover_every_lecture():
    a = section("L1", SectionKind.LECTURE)
    b = section("L2", SectionKind.LECTURE)
    r = section("R1", SectionKind.RECITATION, parent="L2")
    bundles = valid_bundles(Course("X 1", "", [a, b, r]))
    assert [a] in bundles and [b, r] in bundles


def test_has_conflict_detects_overlap_across_sections():
    a = section("A", SectionKind.LECTURE, [m(Day.MON, "10:00", "11:15")])
    b = section("B", SectionKind.LECTURE, [m(Day.MON, "11:00", "12:15")])
    assert has_conflict([a, b])


def test_sections_without_meetings_never_conflict():
    """Online/TBA sections have no meetings, so they cannot collide. Documented
    as a known gap: they also contribute nothing to day/time scoring."""
    a = section("A", SectionKind.LECTURE, [])
    b = section("B", SectionKind.LECTURE, [])
    assert not has_conflict([a, b])


def test_generate_schedules_excludes_conflicting_combinations():
    slot = [m(Day.MON, "10:00", "11:15")]
    other = [m(Day.TUE, "10:00", "11:15")]
    c1 = Course("X 1", "", [section("A", SectionKind.LECTURE, slot)])
    c2 = Course("Y 1", "", [
        section("B", SectionKind.LECTURE, slot),    # clashes with A
        section("C", SectionKind.LECTURE, other),   # fits
    ])

    schedules = generate_schedules([c1, c2])

    assert len(schedules) == 1
    assert {s.section_id for s in schedules[0]} == {"A", "C"}
