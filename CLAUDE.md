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
- Dependencies: `requests`, `beautifulsoup4`, `anthropic`.
- `ANTHROPIC_API_KEY` is exported for the planned LLM narrator (see Known
  gaps — not built yet) and is **session-only**, so a fresh terminal loses
  it. Moving to a `.env` + python-dotenv (gitignored) is a pending cleanup
  regardless of narrator status.
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
| `scoring.py` | `Preferences`, per-factor scoring, `compare_to_ideal`, `rank_schedules` |
| `main.py` | CLI entry point: argparse flags for course codes, term, and preferences |
| `validate_linkage.py` | **Keep.** Guards the recitation-linkage assumption |
| `validate_join.py` | **Keep.** Guards both instructor-source joins (Math + CS) |

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
- **Minutes since midnight** for all times. Makes overlap detection two integer
  comparisons.
- **Normalize every scoring factor to 0..1** before weighting, so incommensurable
  things (a 0-5 rating, a day match, a time-window fit) become comparable. The
  "ideal" schedule is therefore just 1.0 on every factor, and a gap is `1.0 - score`.
- **Weights are relative, not required to sum to 1.** `score_schedule` divides by the
  weight total, so `{PROFESSOR: 3, TIME: 2, DAYS: 1}` works as written.
- **`ScoreBreakdown` returns per-factor scores, not just a total**, because the
  narrator needs the individual factors to describe pros and cons.

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

NYU publishes no lecture-to-recitation relationship anywhere. The heuristic:

> Sections sorted by number form blocks. Each `LEC` starts a block, and every
> following non-lecture section belongs to it until the next `LEC`.

Implemented in `nyu_provider.get_courses` with `current_lecture_id`, and clearly
commented as a replaceable assumption. Sorting must be **numeric** (`_section_order`
extracts digits), because the API returns rows out of order.

Evidence supporting it:
- MATH-UA 121 Fall 2026: 7 lectures at sections 001, 006, 011, 016, 021, 026, 031,
  a perfectly regular stride of 5 (one lecture + four recitations), totaling the 35
  sections the API returns.
- `validate_linkage.py` reports **0 recitations overlapping their own assigned
  lecture** and 0 orphans. This has real power: recitation 020 (Mon 2:00-3:15)
  conflicts with lecture 021 (MW 2:00-3:15), so an off-by-one-block mapping would
  have produced a detectable collision.
- **Independent corroboration:** the Courant department page lists exactly
  001/006/011/016/021/026/031 as the lecture sections, from a source that knows
  nothing of this inference.

Caveat to keep in mind: most recitations meet Friday while lectures do not, so a
mislink between two Friday-free lectures could still escape detection. Run
`validate_linkage.py` whenever a new department is added.

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
  source. Verified 24 of 25 lectures matched across MATH-UA 121, MATH-UA 122,
  CSCI-UA 101, and CSCI-UA 102 (Fall 2026); the one miss (CSCI-UA 102 sec 010)
  is a genuine `TBA` on the department page, not a join failure.

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

### Known gaps

- **Professor quality is inert.** Instructor names exist for both Math and CS,
  but `professor_ratings` is empty, so `_score_professor` returns a neutral 0.5
  for every schedule. A factor that is constant across all candidates
  contributes zero discrimination to the ranking; it only shifts every total
  equally. Either wire up RateMyProfessor or set its weight to 0 in the
  meantime.
- **No narrator.py / no LLM narration exists.** The architecture and design
  principles above describe an "LLM explanation layer with deterministic
  fallback," and the first commit's message claims "AI narration," but no such
  file has ever existed (confirmed via `git ls-tree` on the initial commit).
  `anthropic` is installed and `ANTHROPIC_API_KEY` handling is documented, but
  nothing calls it. All current output (`main.py`'s "strongest"/"biggest
  tradeoff" lines) is deterministic string formatting.
- **Score ties are much rarer but not gone.** With six scoring factors instead
  of three, the top-10 spread for the example course list is 0.67–0.74 rather
  than a flat tie at 0.71. The exact ties that remain are schedules differing
  only in which recitation slot they picked, where those slots happen to have
  identical day/time — a correct result, not a bug.
- **No tests.** Only the two validation scripts.
- **Sections with no meeting times** (online/TBA) cannot conflict with anything and
  contribute nothing to day/time factors, which can inflate their schedules' scores.
- **Course-wide recitations** (`parent_section_id = None`, pairing with any lecture)
  are not handled.
- **Lecture requiring both a lab and a recitation** is not handled;
  `valid_bundles` currently treats each non-lecture as a standalone alternative
  rather than taking a cartesian product across component types. This is the case
  that reportedly crashes other NYU schedulers.

---

## Next steps

1. **RateMyProfessor integration.** No official API; maintained unofficial wrappers
   exist around its GraphQL endpoint. Keep it behind its own `RatingProvider`-style
   interface so the legal/ToS question stays a swappable decision. Shrink or flag
   low-review-count ratings so a single 5-star review doesn't outrank a 4.3 with 100
   reviews. **This is where the genuinely fuzzy entity matching lives** (catalog name
   to RMP profile), unlike the exact Math/CS joins.
2. **Tests**, converting the two validation scripts into real ones.
3. **Build `narrator.py`**, the LLM explanation layer described above — it doesn't
   exist yet (see Known gaps). This has to land before lazy/cached narration (next
   item) makes sense, since that step presupposes narration already exists.
4. **FastAPI route layer** over the existing logic.
5. **React + TypeScript frontend**: weight sliders, ranked results, per-factor score
   bars, expandable AI explanation.
6. Lazy + cached narration (generate on expand, remember the result) rather than
   narrating the whole ranked list.
7. Deploy, with a README containing an architecture diagram and a demo GIF.

Done since the last pass: new scoring factors (days on campus, idle time, late
start), the CS department instructor source, and CLI input on `main.py`.

## Resume framing

The strongest points to articulate: the provider abstraction (adding a university is
one adapter), and the linked-recitation bundling, which was inferred from section
numbering and then validated against meeting-time consistency and an independent
department listing. The deterministic-core-plus-LLM-narrator split is the design
principle that will govern `narrator.py` once it's built (see Next steps) — worth
articulating as an intentional design decision, but don't claim it as shipped.