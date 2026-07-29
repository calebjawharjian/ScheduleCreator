from models import Day, hhmm
from scheduler import generate_schedules
from scoring import (
    Preferences, rank_schedules, compare_to_ideal,
    DAYS, TIME, PROFESSOR, FACTOR_LABELS,
)

# Flip to False to run offline against the hand-built fixtures.
USE_LIVE_DATA = True

FALL_2026 = "1268"
COURSE_CODES = ["CSCI-UA 101", "CSCI-UA 102", "MATH-UA 121"]

if USE_LIVE_DATA:
    from nyu_provider import NYUProvider
    from courant import CourantInstructors
    provider = NYUProvider(instructors=CourantInstructors())
    courses = provider.get_courses(FALL_2026, COURSE_CODES)
else:
    from providers import FixtureProvider
    from fixtures import build_fixture_courses
    provider = FixtureProvider(build_fixture_courses())
    courses = provider.get_courses(FALL_2026, ["MATH-UA 121", "CSCI-UA 102"])

# Loud failure beats a silent empty result.
if not courses:
    raise SystemExit("No courses came back. Check the codes and term.")

found = {c.code for c in courses}
for code in COURSE_CODES if USE_LIVE_DATA else []:
    if code not in found:
        print(f"warning: no sections found for {code}")

print("Loaded:")
for course in courses:
    print(f"  {course.code}  {course.title}  ({len(course.sections)} sections)")
print()

schedules = generate_schedules(courses)
print(f"{len(schedules)} conflict-free schedules\n")
if not schedules:
    raise SystemExit("No valid schedules. Every combination had a time conflict.")

prefs = Preferences(
    preferred_days={Day.MON, Day.WED},
    time_start=hhmm("12:00"),
    time_end=hhmm("18:00"),
    # Placeholder. Real NYU instructor names won't match these, so the
    # professor factor will score a neutral 0.5 until we wire up real ratings.
    professor_ratings={},
    weights={PROFESSOR: 3, TIME: 2, DAYS: 1},
)

TOP_N = 5
for rank, (sched, breakdown) in enumerate(rank_schedules(schedules, prefs)[:TOP_N], 1):
    comparison = compare_to_ideal(breakdown, prefs)
    best = comparison.strengths[0]
    worst = comparison.weaknesses[0]

    print(f"#{rank}  score {comparison.total:.2f}  (gap from ideal {comparison.total_gap:.2f})")
    for s in sched:
        who = s.instructor or "(no instructor listed)"
        print(f"      {s.course_code:<14} {s.section_id:<8} {s.kind.name:<10} {who}")
    print(f"      strongest: {FACTOR_LABELS[best.name]} at {best.score:.2f}")
    print(f"      biggest tradeoff: {FACTOR_LABELS[worst.name]} "
          f"costs {worst.weighted_gap:.2f} of the total")
    print()