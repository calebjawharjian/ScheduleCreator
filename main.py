import argparse
import sys
from models import Day, hhmm
from scheduler import generate_schedules
from scoring import (
    Preferences, rank_schedules, compare_to_ideal, distinct_schedules,
    DAYS, TIME, PROFESSOR, DAYS_ON_CAMPUS, IDLE_TIME, LATE_START, FACTOR_LABELS,
)

# Flip to False to run offline against the hand-built fixtures.
USE_LIVE_DATA = True

# Friendly names on the CLI, translated to NYU's opaque internal srcdb codes.
TERM_CODES = {"fall2026": "1268", "spring2026": "1264"}

DEFAULT_COURSE_CODES = ["CSCI-UA 101", "CSCI-UA 102", "MATH-UA 121"]

# Washington Square to Brooklyn is roughly half an hour door to door on the
# subway or the shuttle. Only ever applied when a schedule actually spans both.
DEFAULT_COMMUTE_MINUTES = 30
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


def _requirement(text: str):
    from constraints import parse_requirement
    try:
        return parse_requirement(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


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
    parser.add_argument("--no-ratings", action="store_true",
                         help="skip RateMyProfessor lookups (offline, faster)")
    parser.add_argument("--require", dest="requirements", type=_requirement,
                         action="append", default=None,
                         help='non-negotiable, e.g. --require "CSCI-UA 102=Anasse Bari"')
    parser.add_argument("--commute-buffer", type=int, default=DEFAULT_COMMUTE_MINUTES,
                         metavar="MINUTES",
                         help="travel time required between campuses "
                              f"(default {DEFAULT_COMMUTE_MINUTES}, only applied "
                              "when a schedule spans campuses)")
    parser.add_argument("--no-commute", action="store_true",
                         help="ignore travel time between campuses")
    return parser.parse_args()


def _clock(minutes: int) -> str:
    """810 -> '13:30'. Inverse of models.hhmm, for display only."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_meetings(meetings) -> str:
    """'Mon Wed 12:30-13:50'. Days sharing a time slot are grouped, since a
    lecture meeting MW at one time should read as one line, not two."""
    if not meetings:
        return "no meeting time"
    by_slot: dict[tuple[int, int], list] = {}
    for m in meetings:
        by_slot.setdefault((m.start, m.end), []).append(m.day)
    parts = []
    for (start, end), days in sorted(by_slot.items()):
        day_text = " ".join(d.name.capitalize() for d in sorted(days))
        parts.append(f"{day_text} {_clock(start)}-{_clock(end)}")
    return ", ".join(parts)


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


def _parse_requirements(raw: str) -> list:
    from constraints import parse_requirement
    # Comma-separates entries; '=' separates course from name, so neither
    # a course code's space nor a professor's space is ambiguous.
    return [parse_requirement(part) for part in raw.split(",") if part.strip()]


def _parse_term(raw: str) -> str:
    key = raw.strip().lower()
    if key not in TERM_CODES:
        raise ValueError(f"unknown term {raw!r}; choose from {sorted(TERM_CODES)}")
    return key


def prompt_for_preferences():
    """Interactive fallback when the program is run with no flags."""
    print("Schedule preferences (press Enter to accept each default).\n")

    course_codes = _ask("Course codes, comma-separated\n", "e.g. " +
                        ", ".join(DEFAULT_COURSE_CODES),
                        _parse_courses, DEFAULT_COURSE_CODES)
    term_name = _ask("Term", "e.g. fall2026", _parse_term, "fall2026")
    days = _ask("Preferred days", "e.g. MON WED", _parse_days, ["MON", "WED"])
    time_start = _ask("Earliest acceptable start time (HH:MM)", "e.g. 12:00",
                      _time, hhmm("12:00"))
    time_end = _ask("Latest acceptable end time (HH:MM)", "e.g. 18:00",
                    _time, hhmm("18:00"))

    requirements = _ask(
        "Required professors, non-negotiable (COURSE=Name, comma-separated)",
        'none, e.g. CSCI-UA 102=Anasse Bari',
        _parse_requirements, [])

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
    return (course_codes, term_name, days, time_start, time_end, weights,
            requirements)


def prompt_for_commute(campuses: set[str]) -> int:
    """Asked only when a schedule actually spans campuses, and only after the
    courses are loaded, since that is the first point we know."""
    print(f"\nYour courses span two campuses: {', '.join(sorted(campuses))}.")
    print("Back-to-back classes across them are impossible to attend.")

    def parse(raw: str) -> int:
        text = raw.strip().lower()
        if text in {"n", "no"}:
            return 0
        if text in {"y", "yes"}:
            return DEFAULT_COMMUTE_MINUTES
        minutes = int(text)
        if minutes < 0:
            raise ValueError("travel time cannot be negative")
        return minutes

    return _ask("Minutes to allow for travel between them? (number, or 'no')",
                str(DEFAULT_COMMUTE_MINUTES), parse, DEFAULT_COMMUTE_MINUTES)


args = parse_args()
interactive = len(sys.argv) == 1

# No flags at all means an interactive run; any flag means scripted, where
# unspecified values fall back to the same defaults as before.
if interactive:
    (course_codes, term_name, day_names, time_start, time_end, weights,
     requirements) = prompt_for_preferences()
else:
    course_codes = args.course_codes or DEFAULT_COURSE_CODES
    term_name = args.term
    day_names = args.days
    time_start, time_end = args.time_start, args.time_end
    weights = {**DEFAULT_WEIGHTS, **dict(args.weights or [])}
    requirements = args.requirements or []

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

# Travel time only matters, and is only asked about, when the courses actually
# span campuses. Detected here because this is the first point the real course
# list is known.
from campuses import campuses_in

campuses = campuses_in(courses)
commute_minutes = 0
if len(campuses) > 1 and not args.no_commute:
    if interactive:
        commute_minutes = prompt_for_commute(campuses)
    else:
        commute_minutes = args.commute_buffer
        print(f"\nCourses span {', '.join(sorted(campuses))}; requiring "
              f"{commute_minutes} minutes between them (--no-commute to ignore).")
print()

# Ratings are resolved up front, so `scoring.py` stays pure: it receives a
# finished {name -> rating} dict and never touches the network itself.
professor_ratings: dict[str, float] = {}
if not args.no_ratings:
    from ratings import RMPRatings, department_hint

    wanted = {}
    for course in courses:
        for s in course.sections:
            if s.instructor:
                wanted.setdefault(s.instructor, department_hint(s.course_code))
    if wanted:
        print(f"Looking up {len(wanted)} instructors on RateMyProfessor...")
        professor_ratings = RMPRatings().ratings_for(wanted)
        print(f"  matched {len(professor_ratings)} of {len(wanted)}\n")

schedules = generate_schedules(courses)
print(f"{len(schedules)} conflict-free schedules")
if not schedules:
    raise SystemExit("No valid schedules. Every combination had a time conflict.")

# Hard requirements filter before scoring. A weight can never express "must":
# it only makes matching schedules score well, so a non-negotiable professor
# can still be outranked on the other factors.
#
# Two tiers. Named professors are never relaxed -- the user called them
# non-negotiable. Travel time is relaxed only if nothing else is left, because
# a student with no commuting-friendly option still has to register for
# something; the compromise is reported rather than hidden.
compromised = []
if requirements or commute_minutes > 0:
    from constraints import (
        CommuteBuffer, RequiredInstructor, best_effort, explain_unsatisfiable)

    # A requirement naming a course you didn't request is incoherent, and left
    # as a warning it is dangerous: a typo'd course code makes the requirement
    # silently vacuous, so you get a ranked list that ignores a non-negotiable.
    loaded = {c.code for c in courses}
    stray = [r for r in requirements
             if isinstance(r, RequiredInstructor) and r.course_code not in loaded]
    if stray:
        raise SystemExit(
            "\n".join([f"Required {r.describe()}, but {r.course_code} is not "
                       f"in your course list." for r in stray]
                      + [f"Requested courses: {', '.join(sorted(loaded))}"]))

    commute = [CommuteBuffer(commute_minutes)] if commute_minutes > 0 else []
    schedules, compromised = best_effort(schedules, requirements, commute)

    if not schedules:
        raise SystemExit(
            "\nNo schedules satisfy your requirements.\n"
            + "\n".join(explain_unsatisfiable(courses, requirements)))

    if compromised:
        print(f"{len(schedules)} best-fit schedules (nothing satisfies "
              f"everything)")
        for constraint in compromised:
            print(f"  compromise: {constraint.describe()} could not be met")
            worst = constraint.violations(schedules[0])
            for line in worst:
                print(f"    {line}")
    else:
        print(f"{len(schedules)} of those satisfy your requirements")
print()

prefs = Preferences(
    preferred_days=preferred_days,
    time_start=time_start,
    time_end=time_end,
    professor_ratings=professor_ratings,
    weights=weights,
)

TOP_N = 10

# Ranked results are collapsed to distinct weeks before display. NYU's catalog
# contains many sections that differ only by CRN -- same day, same time, same
# (usually unpublished) instructor -- so an uncollapsed top ten is routinely the
# same schedule printed ten times while genuinely different options sit hundreds
# of rows below it.
ranked = rank_schedules(schedules, prefs)
distinct = distinct_schedules(ranked)
if len(distinct) < len(ranked):
    print(f"{len(distinct)} distinct weeks (the rest differ only in which "
          f"interchangeable section was picked)\n")

for rank, option in enumerate(distinct[:TOP_N], 1):
    comparison = compare_to_ideal(option.breakdown, prefs)
    best = comparison.strengths[0]
    worst = comparison.weaknesses[0]

    print(f"#{rank}  score {comparison.total:.2f}  (gap from ideal {comparison.total_gap:.2f})")
    for s in sorted(option.schedule, key=lambda s: (s.course_code, s.kind)):
        who = s.instructor or "(no instructor listed)"
        rating = professor_ratings.get(s.instructor)
        if rating is not None:
            who = f"{who} ({rating:.1f}/5)"
        print(f"      {s.course_code:<14} {s.section_id:<8} {s.kind.name:<10} "
              f"{format_meetings(s.meetings):<34} {who}")
    if option.variants > 1:
        others = option.variants - 1
        print(f"      ({others} other section combination"
              f"{'s' if others > 1 else ''} meet at these same times)")
    print(f"      strongest: {FACTOR_LABELS[best.name]} at {best.score:.2f}")
    print(f"      biggest tradeoff: {FACTOR_LABELS[worst.name]} "
          f"costs {worst.weighted_gap:.2f} of the total")
    print()
