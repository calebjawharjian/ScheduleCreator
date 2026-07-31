from models import Day, Meeting, Section, SectionKind, Course, hhmm


def m(day, start, end):
    return Meeting(day, hhmm(start), hhmm(end))


def test_hhmm_converts_to_minutes_since_midnight():
    assert hhmm("00:00") == 0
    assert hhmm("13:30") == 810
    assert hhmm("23:59") == 1439


def test_overlapping_meetings_on_same_day():
    assert m(Day.MON, "10:00", "11:15").overlaps(m(Day.MON, "11:00", "12:15"))


def test_same_time_different_days_do_not_overlap():
    assert not m(Day.MON, "10:00", "11:15").overlaps(m(Day.TUE, "10:00", "11:15"))


def test_back_to_back_meetings_do_not_overlap():
    """The boundary case: one ends exactly when the next starts. Using `<`
    rather than `<=` in Meeting.overlaps is what makes this legal."""
    assert not m(Day.MON, "10:00", "11:00").overlaps(m(Day.MON, "11:00", "12:00"))


def test_overlap_is_symmetric():
    a, b = m(Day.WED, "09:00", "10:30"), m(Day.WED, "10:00", "11:00")
    assert a.overlaps(b) == b.overlaps(a) is True


def test_course_separates_lectures_from_linked_sections():
    lec = Section("L1", "X 1", SectionKind.LECTURE, None)
    rec = Section("R1", "X 1", SectionKind.RECITATION, None, parent_section_id="L1")
    lab = Section("B1", "X 1", SectionKind.LAB, None, parent_section_id="L1")
    other = Section("R2", "X 1", SectionKind.RECITATION, None, parent_section_id="L9")
    course = Course("X 1", "Test", [lec, rec, lab, other])

    assert course.lectures() == [lec]
    # Returns every linked non-lecture kind, not only recitations.
    assert course.recitations_for("L1") == [rec, lab]
