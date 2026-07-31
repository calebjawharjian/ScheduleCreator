# ScheduleCreator

A course-schedule optimizer for NYU. The user supplies the courses they need plus
weighted preferences (days, time window, professor quality), and the app generates
every conflict-free schedule, scores and ranks them, and explains the top few in
plain language.

Personal project first, possibly a product for other students later. NYU only for
now, but the architecture is deliberately built so other universities are a new
data provider rather than a rewrite.

---

## Working style (important)

This project is a learning exercise as much as a deliverable. When helping:

- Work **incrementally**. Build one piece at a time, not whole subsystems at once.
- **Explain the decisions**, not just the code. Why this data structure, why this
  placement, what the tradeoffs were. The reasoning is the point.
- At real forks (library choice, data modeling), lay out the options and let the
  user decide rather than silently picking.
- The user will sometimes ask for a function to be written directly instead of
  attempting it first. That's fine; still explain the decisions fully.
- Flag known gaps explicitly rather than leaving them hidden.

---

## Environment

- **WSL (Ubuntu) on Windows.** Project lives on the Windows filesystem at
  `/mnt/c/Users/calja/source/repos/ScheduleCreator`.
- **Always use the venv's interpreter explicitly:** `./venv/bin/python script.py`.
  The `(venv)` label in the shell prompt is decoration and can lie. `which python3`
  is ground truth; it should resolve inside `venv/bin/`.
- System Python is PEP 668 protected (`externally-managed-environment`). Never use
  `--break-system-packages`; fix the venv instead. A broken venv gets rebuilt:
  `rm -rf venv && python3 -m venv venv && source venv/bin/activate`.
- Dependencies: `requests`, `beautifulsoup4`, `anthropic`, `python-dotenv`, `pytest`.
- **Tests:** `./venv/bin/python -m pytest` runs the offline suite (no network,
  ~1.5s). `pytest -m live` runs the checks that hit NYU's real endpoints. An
  autouse fixture makes any unmarked test that reaches the network fail, so the
  offline suite cannot silently start depending on it.
- **Narrator keys live in `.env`** (gitignored; copy `.env.example`), loaded by
  `narrator.load_env()` before anything reads the environment. This replaced
  session-only `export`s that a fresh terminal silently lost. A real export
  still wins — `load_dotenv(override=False)`. **No key is required to run:**
  narration falls back to deterministic summaries, so an empty `.env` is a
  supported configuration, not a broken one.
- **Commit and push after each session and each significant code change,**
  with a commit message describing what changed. Remote is
  `github.com/calebjawharjian/ScheduleCreator`, branch `main`.

---

## Architecture

Deliberate separation: data sourcing, scheduling logic, scoring, and narration are
independent concerns. Swapping the data source touches nothing downstream.

| File | Responsibility |
| --- | --- |
| `models.py` | Core dataclasses: `Day`, `SectionKind`, `Meeting`, `Section`, `Course` |
| `providers.py` | Abstract `CourseProvider` interface + `FixtureProvider` |
| `fixtures.py` | Hand-built offline course data for development and testing |
| `nyu_provider.py` | Live NYU class-search provider, plus instructor enrichment |
| `ma_instructors.py` | Math dept (Courant) instructor-name source |
| `cs_instructors.py` | CS dept instructor-name source |
| `combined_instructors.py` | Merges multiple instructor sources into one for `NYUProvider` |
| `scheduler.py` | `valid_bundles`, `has_conflict`, `generate_schedules` |
| `campuses.py` | NYU campus constants + the Location filter codes the provider queries |
| `constraints.py` | `Constraint` interface + `RequiredInstructor` + `CommuteBuffer`; hard filters applied before scoring |
| `ratings.py` | `RatingProvider` interface + `RMPRatings` (RateMyProfessor) + `NullRatings` |
| `scoring.py` | `Preferences`, per-factor scoring, `compare_to_ideal`, `rank_schedules`, `distinct_schedules` |
| `narrator.py` | `Narrator` interface + `build_findings` + `TemplateNarrator` (deterministic) + `OpenAICompatNarrator` + `ClaudeNarrator` |
| `main.py` | CLI entry point: argparse flags, interactive prompts when run bare, result display |
| `tests/` | pytest suite. Offline by default; `tests/fixtures/` holds recorded responses |
| `tests/test_live_data.py` | **Keep.** Live guards for linkage + both joins; `pytest -m live` |

### Design principles established

- **Provider abstraction.** `CourseProvider.get_courses(term, course_codes)` is the
  seam. `FixtureProvider` (offline, deterministic) and `NYUProvider` (live) are
  interchangeable; `main.py` has a `USE_LIVE_DATA` toggle. Other universities = new
  provider.
- **Fail loudly on unknown input.** Permissive defaults have caused every major bug
  in this project (an empty fixture list, a silent PATH problem, 221 recitations
  mislabeled as lectures by a `.get(..., LECTURE)` fallback). Unknown section types
  are skipped with a printed warning, never coerced.
- **Deterministic core, LLM narrates only.** Scoring and ranking are pure code. The
  model receives an already-decided findings dict and phrases it. It never scores,
  ranks, or judges. Keeps results reproducible, cheap, and trustworthy.
- **The narrator is given facts it cannot get wrong.** `build_findings` hands
  over durations, day-to-course mappings and counts — **never a clock time.**
  The student registers against those times, so they stay deterministic in
  `main.py`. The payoff is a check that would otherwise be impossible: since
  findings contain no clock time, any `HH:MM` in the model's output was
  fabricated, and `_ChatNarrator` refuses it and falls back. A test asserts
  the no-times property of `build_findings`, because the guard is only sound
  while it holds.
- **Narrate relations, not properties.** "Five days on campus" is a fact a
  template already prints; routing it through a model just paraphrases what is
  on screen. What earns the call is *why*: which courses force each day, what
  brackets the longest gap, which requirement pinned the result, and — via
  `achievable`, the best score anything reached per factor — what was actually
  given up rather than merely scored low.
- **Narration degrades, it does not fail.** No key, no network, a bad response
  or an invented time all fall back to `TemplateNarrator`, whose output is the
  pre-narration format exactly. A deliberate exception to "fail loudly", which
  governs *data*: losing commentary is not the same as getting a fact wrong.
- **Minutes since midnight** for all times. Makes overlap detection two integer
  comparisons.
- **Normalize every scoring factor to 0..1** before weighting, so incommensurable
  things (a 0-5 rating, a day match, a time-window fit) become comparable. The
  "ideal" schedule is therefore just 1.0 on every factor, and a gap is `1.0 - score`.
- **Weights are relative, not required to sum to 1.** `score_schedule` divides by the
  weight total, so `{PROFESSOR: 3, TIME: 2, DAYS: 1}` works as written.
- **`ScoreBreakdown` returns per-factor scores, not just a total**, because the
  narrator needs the individual factors to describe pros and cons.
- **Hard constraints filter; weights only rank.** A weight can never express
  "must have": weights trade off against each other by construction, so
  `professor=1000` merely makes matching schedules score well and one can still
  lose on the other factors. This is not hypothetical — it is how a student who
  wanted one specific CSCI professor saw him ranked near the bottom, because he
  taught on a non-preferred day. Requirements therefore live in `constraints.py`
  and remove candidates *before* `rank_schedules` ever sees them. Constraints
  decide what is eligible; scoring decides what is best among the eligible.
- **Constraints come in two tiers, and only the lower one bends.** A named
  professor is a stated non-negotiable and is never relaxed — if it cannot be
  met, that is a real dead end worth reporting. Travel time is physical
  reality, but a student with no commuting-friendly option still has to
  register for something, so `best_effort` falls back to the schedules that
  come *closest*, ranked by `Constraint.penalty` (minutes short), and names the
  compromise rather than hiding it. Silently dropping either would be worse
  than both failing loudly and returning a flawed-but-labeled answer.
- **Rank concrete schedules, but display distinct *weeks*.** A schedule is a
  set of CRNs; what a student chooses between is a set of times. NYU's catalog
  makes those wildly different quantities — 25 of EG-UY 1004's lecture sections
  meet at the identical hour with no published instructor, so they are
  interchangeable by construction. Ranking them separately is correct and must
  stay that way (they really are different registrations), but *showing* them
  separately fills the top ten with one answer. `distinct_schedules` collapses
  on `schedule_shape` at the display boundary only, keeping the deterministic
  core untouched. **The shape must contain every field `score_schedule` reads**
  — meetings and lecture instructors — or collapsing would merge schedules with
  different scores and hide the better one behind whichever the cartesian
  product happened to emit first. Campus and non-lecture instructors are in the
  shape too: scoring ignores them, but adding a field can only split a group,
  never wrongly merge one, so over-including is the safe direction.

---

## Data sources: hard-won facts

These were established empirically. Do not re-derive or assume otherwise.

### NYU public class search API (authoritative for sections and times)

```
POST https://bulletins.nyu.edu/class-search/api/?page=fose&route=search&subject=CSCI-UA
body: {"other": {"srcdb": "1268"}, "criteria": [{"field": "subject", "value": "CSCI-UA"}]}
```

- **Term codes (`srcdb`):** `1268` = Fall 2026, `1264` = Spring 2026. The value
  `9999` is a "default/all" sentinel that returns far more rows; always pass an
  explicit term.
- **Searches by subject, not by course.** Derive subjects from requested course
  codes, fetch once per subject, then filter. Results are cached per
  `(term, subject)` to avoid hammering a public service.
- **`schd` (component) codes:** `LEC`, `RCT`, `LAB`, `INT`, `IND`.
  It is **`RCT`, not `REC`.** `INT` and `IND` (internship, independent study) are
  skipped.
- **`meetingTimes` is a JSON *string* nested inside the JSON response**, so it needs
  its own `json.loads`.
- **`meet_day` is 0-indexed from Monday**, which matches the `Day` enum exactly, no
  translation needed. `start_time` can be 3 digits (`"930"`), so `zfill(4)` first.
- **`crn` is the unique section ID; `no` is the section number** (`"001"`), which is
  the join key for department listings.
- **`instr` is ALWAYS EMPTY.** Verified 0 of 652 sections across CSCI-UA, ECON-UA,
  PSYCH-UA, HIST-UA, CHEM-UA for Fall 2026, and 0 of 274 for the already-completed
  Spring 2026 term. NYU's public search does not publish instructors. Do not build
  anything that expects this field to populate.

### Details endpoint (checked, not useful for linkage)

```
POST https://bulletins.nyu.edu/class-search/api/?page=fose&route=details
body: {"group": "code:MATH-UA 121", "key": "crn:10443", "srcdb": "1268", "matched": "crn:10443"}
```

Returns `description`, `clssnotes`, `registration_restrictions`, `meeting_html`, and
`allInGroup` / `all_sections`. **`allInGroup` is every section of the course, not the
sections linked to the requested one.** There is no instructor field. So this
endpoint does not solve linkage or instructors. Useful later for prerequisites.

### Recitation linkage: INFERRED, not published

NYU publishes no lecture-to-recitation relationship anywhere. **Two numbering
schemes exist and they need two different strategies.** Both live in
`nyu_provider._link_sections`.

**1. Stem-based (Tandon).** Sections are named block stem + component code:
`A1LC` is the lecture of block `A1`, `A1LB` its lab, `A1RC` its recitation.
The block is named explicitly, so linkage is a dictionary lookup and ordering
is irrelevant. Used only when *every* section in the course parses as a stem,
so mixed/unknown numbering falls back rather than half-applying a scheme.

**2. Sequential (CAS).** Numeric sections (`001` lecture, `002`-`005`
recitations) name no block, so the heuristic is:

> Sections sorted by number form blocks. Each `LEC` starts a block, and every
> following non-lecture section belongs to it until the next `LEC`.

Sorting must be **numeric** (`_section_order` extracts digits), because the API
returns rows out of order.

**Why the split is mandatory, not cosmetic.** Applying the sequential scan to
Tandon numbering is silently and catastrophically wrong: `A1LB` sorts *before*
`A1LC` (same digits, so lexicographic order decides, and `LB` < `LC`), so every
lab gets attached to the **previous** block's lecture — `B1LB → A1LC`,
`C1LB → B1LC` — and the very first lab, having no preceding lecture, is orphaned
and silently dropped from every bundle. This shipped undetected because the
overlap check cannot see it: `B1LB` (Wed 08:00-10:50) does not overlap `A1LC`
(Mon 12:30-13:50), so nothing collides. It also *printed* its findings rather
than asserting them, so "0 self-conflicts" scrolled by while every lab was
mislinked. This is exactly the blind spot the caveat below warned about, found
in the wild on EG-UY 1004.

Evidence supporting it:
- MATH-UA 121 Fall 2026: 7 lectures at sections 001, 006, 011, 016, 021, 026, 031,
  a perfectly regular stride of 5 (one lecture + four recitations), totaling the 35
  sections the API returns.
- The live linkage tests report **0 recitations overlapping their own assigned
  lecture** and 0 orphans. This has real power: recitation 020 (Mon 2:00-3:15)
  conflicts with lecture 021 (MW 2:00-3:15), so an off-by-one-block mapping would
  have produced a detectable collision.
- **Independent corroboration:** the Courant department page lists exactly
  001/006/011/016/021/026/031 as the lecture sections, from a source that knows
  nothing of this inference.

Caveat to keep in mind: most recitations meet Friday while lectures do not, so a
mislink between two Friday-free lectures could still escape detection by the
overlap test alone. The live tests now also check that a section's block stem
matches its parent lecture's stem, which catches cross-block mislinks regardless
of meeting times — but only where sections are stem-numbered. For CAS numeric
sections the overlap test is still the only guard. Run `pytest -m live` whenever
a new department is added, and add its course to `LINKAGE_COURSES` in
`tests/test_live_data.py`.

### Math department instructor source (Courant)

```
GET https://math.nyu.edu/dynamic/courses/course-schedules/?semester=fall-2026
```

- HTML table, scraped with BeautifulSoup. **The page defaults to Summer 2026**, so
  always pass the semester slug.
- Each cell embeds its own mobile label inside `span.header-text`; strip those spans,
  then take the text after the first `:`.
- Catalog format is `MATH-UA.0121-001` → subject, zero-padded catalog number,
  section. `int("0121")` drops the padding to give `MATH-UA 121`.
- Cross-listed rows contain two codes in one cell
  (`MATH-GA.2010-001 CSCI-GA 2420.001`); the regex uses `search`, so the first wins.
- Tandon rows use different prefixes and non-numeric sections (`MA-GY.6213-I`).
- **Only primary lecture sections are listed**, which is exactly what the professor
  factor scores (`_score_professor` considers lectures only, since recitations are
  usually staff-taught).
- **Ignore Courant's meeting times.** The class-search API is authoritative and
  already structured; Courant's `11:10-1:15PM` is ambiguous about the start meridiem.
  Use each source only for what it is authoritative about. Courant's times are still
  useful as a one-time cross-check that the join lined up.
- The join is **exact** on `(course_code, section_no)`. No fuzzy name matching
  required here. Verified 7 of 7 for MATH-UA 121 Fall 2026.

### CS department instructor source

```
GET https://cs.nyu.edu/dynamic/courses/schedule/?semester=fall_2026
```

- **Not a `<table>`** — it's a list of `<li class="row" id="csci-ua0101-001">`
  elements, one per section. Scraped by selecting `li.row` directly, not by
  finding a table and its rows.
- Semester slug uses an **underscore** (`fall_2026`), unlike Courant's dashed
  `fall-2026`. Easy to get wrong copy-pasting the other module's format.
- Catalog format matches Courant's shape (`CSCI-UA.0101-001`), but the anchor
  text embeds a **literal zero-width space** (`&#8203;`) right before the
  section number. It's invisible but not whitespace to regex `\s`, so it has
  to be stripped explicitly (`.replace("​", "")`) or the section number
  parses as `"​001"` and the join silently fails.
- The professor field uses placeholder text instead of a name: `TBA`,
  `STAFF`, and `CANCELLED` (this last one not present on the Courant page,
  since Courant only lists primary lectures which are cancelled less often).
  All three are filtered out rather than joined in as if they were instructors.
- No co-taught/multi-instructor sections found in this snapshot, so — like
  Courant — one name per section is enough.
- The join is **exact** on `(course_code, section_no)`, same shape as the Math
  source. Verified 25 of 25 lectures matched across MATH-UA 121, MATH-UA 122,
  CSCI-UA 101, and CSCI-UA 102 (Fall 2026). It was 24 of 25 when first built —
  CSCI-UA 102 sec 010 read `TBA` and has since been filled in with a real name.
  Useful reminder that these pages are live and a "miss" is often the
  department not having decided yet, not a broken join.

### Campus: from NYU's own Location filter (the class search knows)

The search *response* carries no campus, location, or building field. But the
search *form* has a Location dropdown, and its values can be passed back as a
`camp` criterion:

```
POST .../class-search/api/?page=fose&route=search&subject=MATH-UA
body: {"other": {"srcdb": "1268"}, "criteria": [
         {"field": "subject", "value": "MATH-UA"},
         {"field": "camp",    "value": "WS@BRKLN,WS@INDUS"}]}
```

Location codes come from `select#crit-camp` on `bulletins.nyu.edu/class-search/`.
Brooklyn is `WS@BRKLN,WS@INDUS`.

**An earlier version inferred campus from the school code (`UY`/`GY` = Brooklyn)
and was WRONG.** NYU runs Washington Square-coded courses in Brooklyn: 9 MATH-UA
sections in Fall 2026. The inference called them Manhattan and silently
permitted impossible schedules. Do not reintroduce it.

- **Campus is per SECTION, not per course.** MATH-UA 140 has 32 sections:
  021 and 022 meet in Brooklyn, the other 30 at Washington Square. Anything
  keyed by course code structurally cannot represent this. `Section.campus`
  holds it, filled in by the provider.
- **The Washington Square filter is NOT the complement of Brooklyn.** Its
  `WS*` wildcard also matches `WS@BRKLN`, so it returned all 309 MATH-UA rows
  including the 9 Brooklyn ones — overlap of exactly 9, "in neither" of 0.
  Query **Brooklyn only** and treat everything else as Washington Square; that
  is the only reading that partitions correctly.
- Costs **one extra request per subject**, cached alongside the unfiltered one
  (the cache key includes `camp`).
- **A cross-campus back-to-back is not a time conflict.** Manhattan 12:15 →
  Brooklyn 12:30 has no interval overlap, so `has_conflict` accepts it, but no
  one can attend both. Verified live: for `["CSCI-UA 101", "EG-UY 1004"]`,
  38 of 129 "conflict-free" schedules were physically unattendable.
- **Only transitions between classes are constrained**, never the trip to the
  first class or home from the last. Those are the student's own time.
- **Sections with no campus** (online, abroad, untagged) impose no travel time
  and are skipped, so an online class between two Manhattan classes never looks
  like a round trip.
- **A large buffer can still leave many schedules**, because a schedule whose
  campuses fall on different days has no same-day transition to constrain. Not
  a bug — a 10-hour buffer legitimately left 57 of 129 schedules.

### RateMyProfessor (ratings)

```
POST https://www.ratemyprofessors.com/graphql
headers: {"Authorization": "Basic dGVzdDp0ZXN0"}   # anonymous credential the site ships
```

**No official API.** This is their internal endpoint and is very likely against
their ToS — fine for a personal project, a real question if this ships. That is
exactly why it lives behind `RatingProvider` in `ratings.py`; swapping in a
licensed source or `NullRatings` touches nothing else.

- **`schoolID` must be pinned.** Searching "New York University" also returns
  NYU Wagner and NYU Los Angeles, which are separate schools with separate
  faculty. `New York University` is `U2Nob29sLTY3NQ==`.
- **Search by surname, not full name.** The `text` query is a loose search;
  full names return fewer usable hits than the surname plus local filtering.
- **This is the only inexact join in the project.** Math/CS instructors join on
  `(course_code, section_no)`, an identifier both sides share. NYU and RMP share
  nothing but a human name, so this is real entity matching.
- **NYU publishes middle names, RMP usually does not** (`Hilbert Gene Locklear`
  vs `Hilbert Locklear`), so matching uses first + last only.
- **RMP's own data has dirty whitespace**: `firstName` values like `"Tobias  "`
  with trailing or doubled spaces. Normalize before comparing.
- **An unreviewed profile reports `avgRating: 0`, which is NOT a rating of zero.**
  Scoring it as one would rank an unreviewed professor as the worst possible.
  Sections with `numRatings == 0` are treated as unrated. There are also junk
  profiles (`foster provost`, Business, 0 reviews) that first-name matching
  filters out anyway.
- **Ambiguity is refused, not guessed.** Same surname is resolved by first name;
  a remaining tie is broken by department (`SUBJECT_DEPARTMENTS` maps `CSCI-UA`
  to `Computer Science`); anything still ambiguous is left unrated with a
  warning. A rating attached to the wrong person actively misranks schedules,
  which is worse than the neutral 0.5 that no rating produces.
- **Not every professor is on RMP.** Federico Sichetti (CSCI-UA 102) returns
  zero candidates. Verified 9 of 10 instructors matched for
  `["CSCI-UA 102", "MATH-UA 121"]` Fall 2026.
- Responses are cached to `.rmp_cache.json` (gitignored) and requests are spaced
  by a delay, since this is not a service we have permission to hammer.

### Rejected / dead ends

- **Schedge** (`nyu.a1liu.com`, BUGS-NYU): stale. NYU added reCAPTCHA to its upstream
  source; the newest dependent app only reaches Fall 2023. Not viable for current
  terms.
- **NYU official Course System API**: real, but access requires a request through
  `dataaccess.it.nyu.edu`, a Service Account, an Architecture Review Board meeting,
  and Data Steward approval. Worth asking `API-SUPPORT@NYU.EDU` about, not a
  dependency.
- **Albert login / credential-based access**: explicitly out of scope. No public
  OAuth exists, so it would mean handling NetID passwords that unlock email,
  financial aid, and health records. Unacceptable liability and almost certainly
  against NYU acceptable use. Do not build this.
- **Per-department Google Docs**: some CAS departments publish instructors this way.
  Deferred; ad-hoc formats, unstable, low value relative to effort.

---

## Current state

Working end to end against live Fall 2026 data: courses load, bundles enumerate,
conflicts are detected, schedules are scored and ranked, and the top few are
explained.

For `["CSCI-UA 101", "CSCI-UA 102", "MATH-UA 121"]` in Fall 2026 this produces 1327
conflict-free schedules, with both Math and CS lecture instructors populated.
Course codes, term, preferred days, time window, and factor weights are all
CLI flags on `main.py` now (`--course`, `--term`, `--days`, `--time-start`/
`--time-end`, `--weight`); only `professor_ratings` is still hardcoded empty.
Running `main.py` with **no flags** prompts for each of those interactively
instead (blank line accepts the shown default, bad input re-prompts).

Multi-component courses work: `EG-UY 1004` (lecture + lab + recitation) yields
25 bundles, 24 with all three components and one with lecture+lab, matching a
block that genuinely has no recitation.

Each section prints its meeting days and times alongside the CRN and
instructor. That is deliberate and should stay deterministic even once
`narrator.py` exists: times are facts the user registers against, so they must
never depend on an LLM phrasing them correctly. The narrator's job is the
tradeoff commentary on top ("a three-day week, but a two-hour hole Tuesday").

### Known gaps

- **Rating coverage is partial and unverifiable.** Ratings now flow from RMP
  (9 of 10 instructors matched on the example course list), but a professor
  absent from RMP, unreviewed, or ambiguous is scored a neutral 0.5 — so the
  factor still fails to discriminate between those schedules. There is also no
  way to confirm a match is the *right* person beyond name + department; the
  strict matching reduces that risk rather than eliminating it.
- **Narration has never run against a real provider.** `narrator.py` exists and
  the whole path is verified — but with a local stand-in server, not a hosted
  model, because no key was available when it was built. The transport, request
  shape, response parsing, guard and fallback are all confirmed; **the quality
  of real model output is not.** Expect the system prompt to need tuning on
  first contact, especially "never write a clock time", which a weaker free-tier
  model may well violate. That failure is safe (the guard falls back) but it
  would mean losing narration rather than getting bad narration.
- **Narration is not cached.** Every run re-narrates, so re-running the same
  course list pays for the same prose twice. Harmless while N is 3 and the
  provider is free; the fix is roadmap item 4, and the per-schedule call shape
  was chosen to make it a cache rather than a rewrite.
- **Free-tier model names drift.** `PROVIDERS` pins a default model per
  provider and these get retired regularly. `NARRATOR_MODEL` overrides without
  a code change; a stale default surfaces as a fallback-to-template warning,
  not a crash.
- **Score ties are real, and they are mostly the catalog's fault.** With six
  factors the top-10 spread for the three-course example is 0.67–0.74. On a
  four-course list, though, 78 schedules tied at the top score and covered only
  3 distinct weeks — the ties are near-entirely sections that are genuinely
  interchangeable, which `distinct_schedules` now collapses on display. Ties
  that survive collapsing are different weeks scoring equally, which is a
  correct result, not a bug.
- **Coverage is behavioral, not exhaustive.** 151 offline tests plus 9 live ones
  cover the scoring factors, duplicate collapsing, both linkage strategies,
  bundling, campus tagging, constraints, and both scrapers. `main.py` itself is
  untested (argparse wiring, prompts, display formatting), and there is no
  coverage measurement.
- **Sections with no meeting times** (online/TBA) cannot conflict with anything and
  contribute nothing to day/time factors, which can inflate their schedules' scores.
- **Course-wide recitations** (`parent_section_id = None`, pairing with any lecture)
  are not handled.
- **Schedule counts still explode; the top-N no longer shows it.** EXPOS-UA 1
  alone has 86 sections, so `["CSCI-UA 102", "MATH-UA 121", "EXPOS-UA 1",
  "EG-UY 1004"]` produces 120,119 schedules. `distinct_schedules` fixes the
  *display* (see the design principle), but generation and scoring still walk
  every combination, so runtime and memory scale with the raw count, not the
  462 weeks that survive collapsing.
- **Collapsing does not surface genuinely different tradeoffs.** It removes
  duplicates, not sameness. For the course list above with Bari required, all
  ten displayed weeks are still 5-day weeks differing by one recitation slot,
  because ranking is honest: `days_on_campus` carries 1 of 9 total weight, so
  the best **4-day** week sits at rank 583 (0.624) and the best **3-day** week
  at rank 1984 (0.575), against 0.668 at the top. Both exist and neither is
  reachable by scrolling a top-10. Reserving top-N slots for schedules that are
  *different* rather than merely next-best is the open half of this problem.

---

## Next steps

1. **Point narration at a real provider and tune the prompt.** Everything is
   built and verified against a stand-in; put a free key in `.env` and read
   what an actual model writes (see Known gaps). This is a prompt-quality
   pass, not a code one.
2. **Top-N diversity.** De-duplication is done (`distinct_schedules`); spreading
   is not. The ten distinct weeks shown are still ten variations on one shape,
   while the best 3-day and 4-day weeks sit hundreds of rows down (see Known
   gaps). Reserve slots for schedules that differ *structurally* — fewest days,
   latest start, best professor — so the list shows real alternatives rather
   than the same tradeoff ten times.
3. **FastAPI route layer** over the existing logic.
4. **React + TypeScript frontend**: weight sliders, ranked results, per-factor score
   bars, expandable AI explanation.
5. Lazy + cached narration (generate on expand, remember the result) rather than
   narrating the whole ranked list. The per-schedule call shape already fits
   this; it needs a cache keyed on `schedule_shape`, not a restructure.
6. Deploy, with a README containing an architecture diagram and a demo GIF.

Done since the last pass: RateMyProfessor ratings behind a `RatingProvider`
interface, with strict name matching and Bayesian shrinkage — the professor
factor now discriminates instead of returning a constant 0.5. Plus
`constraints.py`: non-negotiable requirements (`--require "CSCI-UA 102=Anasse
Bari"`) that filter before scoring, and `CommuteBuffer`, which rejects
cross-campus back-to-backs that are physically unattendable. The second
constraint needed no changes to the seam the first one established, which is
the evidence that seam was worth building.

Then campus was corrected: it now comes from NYU's own Location filter and is
tagged per section, replacing a school-code inference that was wrong for 9
MATH-UA sections. Plus `best_effort`, which returns the closest-fitting
schedules (with the compromise named) when travel time cannot be satisfied,
instead of returning nothing. The "best fit" wording it produces is written to
be picked up by `narrator.py` later.

Most recently, `distinct_schedules`. A four-course run returned a top ten that
was **literally one week printed ten times**: 8538 schedules collapsed to 462
distinct weeks, and 78 tied at the top score across just 3 shapes. The
three-course example in this doc has *zero* redundancy (1327 → 1327), which is
exactly why the problem hid for so long — the documented example structurally
could not show it. Fixed at the display boundary rather than in ranking, so the
core stays deterministic and every real registration option is still
enumerated.

Then `narrator.py`, which had been described in this document since the first
commit without ever existing. Built provider-agnostic and free-first:
`OpenAICompatNarrator` covers Groq, Gemini, OpenRouter, DeepSeek and a local
Ollama through one `requests` POST, since they all speak the same
chat-completions shape, and `ClaudeNarrator` handles Anthropic. Selection is by
whichever key `.env` supplies, free before paid, template if none. The design
work that mattered was not the transport but deciding what the model is allowed
to know — see the three narrator principles above.

## Resume framing

The strongest points to articulate: the provider abstraction (adding a university is
one adapter), and the linked-recitation bundling, which was inferred from section
numbering and then validated against meeting-time consistency and an independent
department listing. The deterministic-core-plus-LLM-narrator split is now
shipped rather than aspirational, and the sharpest way to tell it is the
constraint that falls out of it: the model is never given a clock time, so a
time in its output is provably fabricated and gets rejected automatically.
That is a verifiable safety property produced by a design decision, which is a
better story than "we added AI." Be accurate about the limit, though —
narration has only run against a stand-in provider so far (see Known gaps).