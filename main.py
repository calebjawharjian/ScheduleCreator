import argparse
import sys
from models import Day, hhmm
from scheduler import generate_schedules
from scoring import (
    Preferences, rank_schedules, compare_to_ideal,
    DAYS, TIME, PROFESSOR, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START, FACTOR_LABELS,
)

# Flip to False to run offline against the hand-built fixtures.
USE_LIVE_DATA = True

# Friendly names on the CLI, translated to NYU's opaque internal srcdb codes.
TERM_CODES = {"fall2026": "1268", "spring2026": "1264"}

DEFAULT_COURSE_CODES = ["CSCI-UA 101", "CSCI-UA 102", "MATH-UA 121"]
DEFAULT_WEIGHTS = {PROFESSOR: 3, TIME: 2, DAYS: 1,
                   DAYS_ON_CAMPUS: 1, IDLE_TIME: 1, LATE_START: 1}


def _time(text: str) -> int:
    try:
        return hhmm(text)
    except (ValueError, IndexError):
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {text!r}")


def _weight(text: str) -> tuple[str, float]:
    name, _, value = text.partition("=")
    if name not in FACTOR_LABELS:
        raise argparse.ArgumentTypeError(
            f"unknown factor {name!r}; choose from {sorted(FACTOR_LABELS)}")
    try:
        return name, float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and rank NYU schedules.")
    parser.add_argument("--course", dest="course_codes", action="append", default=None,
                         help='repeatable, e.g. --course "CSCI-UA 101"')
    parser.add_argument("--term", choices=sorted(TERM_CODES), default="fall2026")
    parser.add_argument("--days", nargs="+", choices=[d.name for d in Day],
                         default=["MON", "WED"])
    parser.add_argument("--time-start", type=_time, default=hhmm("12:00"))
    parser.add_argument("--time-end", type=_time, default=hhmm("18:00"))
    parser.add_argument("--weight", dest="weights", type=_weight, action="append",
                         default=None, help="repeatable, e.g. --weight professor=3")
    return parser.parse_args()


def _ask(question: str, default_display: str, parse, default_value):
    """Prompt until the answer parses. A blank line accepts the default.

    Reuses the same parse functions as the flags, so both input paths
    validate identically instead of drifting apart.
    """
    while True:
        try:
            raw = input(f"{question}\n  [{default_display}]: ").strip()
        except EOFError:
            print()
            return default_value
        if not raw:
            return default_value
        try:
            return parse(raw)
        except (argparse.ArgumentTypeError, ValueError, KeyError) as exc:
            print(f"  {exc}")


def _parse_courses(raw: str) -> list[str]:
    # Comma-separated here rather than space-separated: course codes contain
    # a space ('CSCI-UA 101') but never a comma, so this stays unambiguous.
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    if not codes:
        raise ValueError("enter at least one course code")
    return codes


def _parse_days(raw: str) -> list[str]:
    names = [d.strip().upper() for d in raw.replace(",", " ").split() if d.strip()]
    valid = {d.name for d in Day}
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(f"unknown day(s) {unknown}; choose from {sorted(valid)}")
    if not names:
        raise ValueError("enter at least one day")
    return names


def _parse_term(raw: str) -> str:
    key = raw.strip().lower()
    if key not in TERM_CODES:
        raise ValueError(f"unknown term {raw!r}; choose from {sorted(TERM_CODES)}")
    return key


def prompt_for_preferences():
    """Interactive fallback when the program is run with no flags."""
    print("Schedule preferences (press Enter to accept each default).\n")

    course_codes = _ask("Course codes, comma-separated",
                        ", ".join(DEFAULT_COURSE_CODES),
                        _parse_courses, DEFAULT_COURSE_CODES)
    term_name = _ask("Term", "fall2026", _parse_term, "fall2026")
    days = _ask("Preferred days", "MON WED", _parse_days, ["MON", "WED"])
    time_start = _ask("Earliest acceptable start time (HH:MM)", "12:00",
                      _time, hhmm("12:00"))
    time_end = _ask("Latest acceptable end time (HH:MM)", "18:00",
                    _time, hhmm("18:00"))

    weights = dict(DEFAULT_WEIGHTS)
    answer = _ask("Customize factor weights? (y/N)", "N",
                  lambda s: s.strip().lower(), "n")
    if answer.startswith("y"):
        print("\n  Higher weight = matters more. Relative, so they need not sum to 1.")
        for name in DEFAULT_WEIGHTS:
            weights[name] = _ask(f"  Weight for {FACTOR_LABELS[name]}",
                                 str(DEFAULT_WEIGHTS[name]),
                                 float, DEFAULT_WEIGHTS[name])
    print()
    return course_codes, term_name, days, time_start, time_end, weights


args = parse_args()

# No flags at all means an interactive run; any flag means scripted, where
# unspecified values fall back to the same defaults as before.
if len(sys.argv) == 1:
    course_codes, term_name, day_names, time_start, time_end, weights = \
        prompt_for_preferences()
else:
    course_codes = args.course_codes or DEFAULT_COURSE_CODES
    term_name = args.term
    day_names = args.days
    time_start, time_end = args.time_start, args.time_end
    weights = {**DEFAULT_WEIGHTS, **dict(args.weights or [])}

term = TERM_CODES[term_name]
preferred_days = {Day[name] for name in day_names}

if USE_LIVE_DATA:
    from nyu_provider import NYUProvider
    from ma_instructors import MAInstructors
    from cs_instructors import CSInstructors
    from combined_instructors import CombinedInstructors
    provider = NYUProvider(
        instructors=CombinedInstructors(MAInstructors(), CSInstructors())
    )
else:
    from providers import FixtureProvider
    from fixtures import build_fixture_courses
    provider = FixtureProvider(build_fixture_courses())

courses = provider.get_courses(term, course_codes)

# Loud failure beats a silent empty result.
if not courses:
    raise SystemExit("No courses came back. Check the codes and term.")

found = {c.code for c in courses}
for code in course_codes:
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
    preferred_days=preferred_days,
    time_start=time_start,
    time_end=time_end,
    # Placeholder. Real NYU instructor names won't match these, so the
    # professor factor will score a neutral 0.5 until we wire up real ratings.
    professor_ratings={},
    weights=weights,
)

TOP_N = 10
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
