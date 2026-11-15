import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchMetadata, generate, rank } from "./api";
import { AlternativeList } from "./components/AlternativeList";
import { Controls } from "./components/Controls";
import { CourseInput, withDraft } from "./components/CourseInput";
import { WeekGrid } from "./components/WeekGrid";
import { formatTerm } from "./format";
import { defaultTiers, tiersToWeights } from "./weights";
import type {
  Alternative, CourseEntry, GenerateResponse, Metadata, RankResponse,
} from "./types";

const EXAMPLE = "CSCI-UA 102, MATH-UA 121, CSCI-UA 4xx";

/** A blank box means "no bound", not zero. */
const parseCredits = (text: string): number | null =>
  text.trim() === "" ? null : Number(text);

/** Everything that changes which schedules exist. When the inputs no longer
 *  match the last search, the results describe a request the user has since
 *  edited, and the page has to say so rather than look current. */
const searchKey = (term: string, entries: CourseEntry[], min: string, max: string) =>
  JSON.stringify([term, entries, min.trim(), max.trim()]);

// An empty time window means "no restriction", not midnight-to-midnight as a
// literal preference -- so a blank picker (the default) and a full-day one
// score identically. Kept out of state so the inputs themselves can stay
// genuinely empty until the user sets a window.
const FULL_DAY_START = "00:00";
const FULL_DAY_END = "23:59";

export default function App() {
  const [meta, setMeta] = useState<Metadata | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  // Empty until /factors names the default, so the term list lives only in the API.
  const [term, setTerm] = useState("");
  const [entries, setEntries] = useState<CourseEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [creditMin, setCreditMin] = useState("");
  const [creditMax, setCreditMax] = useState("");
  const [generation, setGeneration] = useState<GenerateResponse | null>(null);
  const [generating, setGenerating] = useState(false);
  const [searchedKey, setSearchedKey] = useState<string | null>(null);
  const stale = generation !== null &&
    searchedKey !== searchKey(term, withDraft(entries, draft), creditMin, creditMax);

  const [preferredDays, setPreferredDays] = useState<string[]>([]);
  const [timeStart, setTimeStart] = useState("");
  const [timeEnd, setTimeEnd] = useState("");
  const [tiers, setTiers] = useState<string[][]>([]);
  const weights = useMemo(() => tiersToWeights(tiers), [tiers]);
  const [required, setRequired] = useState<Record<string, string>>({});
  const [commuteMinutes, setCommuteMinutes] = useState(30);
  // A ranking preference, so it re-ranks live like the other controls.
  // Replaced by the API's default as soon as /factors answers.
  const [creditTarget, setCreditTarget] = useState(16);

  const [results, setResults] = useState<RankResponse | null>(null);
  const [selected, setSelected] = useState(1);
  const [ranking, setRanking] = useState(false);

  useEffect(() => {
    fetchMetadata()
      .then((m) => {
        setMeta(m);
        setTerm(m.default_term);
        setCreditTarget(m.default_credit_target);
        setTiers(defaultTiers(m.factors.map((f) => f.name)));
      })
      .catch((error: Error) => setApiError(error.message));
  }, []);

  const onGenerate = async () => {
    // Fold in whatever is still being typed: see CourseInput's `draft` prop.
    const courses = withDraft(entries, draft);
    if (courses.length === 0 || !term) return;
    setEntries(courses);
    setDraft("");
    setGenerating(true);
    setApiError(null);
    setResults(null);
    try {
      const body = await generate(term, courses, {
        min: parseCredits(creditMin), max: parseCredits(creditMax),
      });
      setGeneration(body);
      setSearchedKey(searchKey(term, courses, creditMin, creditMax));
      setRequired({});
      // Only impose a travel buffer when the courses actually span campuses.
      setCommuteMinutes(body.campuses.length > 1 ? 30 : 0);
    } catch (error) {
      setApiError((error as Error).message);
      setGeneration(null);
    } finally {
      setGenerating(false);
    }
  };

  // Re-ranking is the interactive path: ~0.14s server-side, so a short debounce
  // is enough to stop a slider drag firing a request per pixel. The abort
  // matters as much as the debounce -- without it a slow reply can land after a
  // newer one and show a ranking that disagrees with the controls.
  const inFlight = useRef<AbortController | null>(null);
  const runRank = useCallback(() => {
    if (!generation) return;
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setRanking(true);

    rank({
      generation_id: generation.generation_id,
      preferred_days: preferredDays,
      time_start: timeStart || FULL_DAY_START,
      time_end: timeEnd || FULL_DAY_END,
      weights,
      credit_target: creditTarget,
      required,
      commute_minutes: commuteMinutes,
      limit: 10,
      narrate: 0,
    }, controller.signal)
      .then((body) => {
        setResults(body);
        setSelected(1);
        setApiError(null);
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") setApiError(error.message);
      })
      .finally(() => {
        if (inFlight.current === controller) setRanking(false);
      });
  }, [generation, preferredDays, timeStart, timeEnd, weights, creditTarget,
      required, commuteMinutes]);

  useEffect(() => {
    if (!generation) return;
    const timer = setTimeout(runRank, 180);
    return () => clearTimeout(timer);
  }, [generation, runRank]);

  // Courses every schedule contains: a required group with exactly one
  // possible course. Alternatives are labelled by what they add beyond these,
  // because two rows with different electives otherwise look alike.
  const fixedCourses = useMemo(() => new Set(
    (generation?.groups ?? [])
      .filter((g) => g.required && g.matches.length === g.count)
      .flatMap((g) => g.matches)), [generation]);

  const current: Alternative | undefined =
    results?.alternatives.find((a) => a.rank === selected)
    ?? results?.alternatives[0];

  return (
    <div className="app">
      <header>
        <h1>ScheduleCreator</h1>
        <p className="tagline">
          Every conflict-free NYU schedule, ranked by what you actually care about.
        </p>
      </header>

      <div className="setup">
        <div className="grow field">
          <span>Courses</span>
          <CourseInput
            entries={entries}
            onChange={setEntries}
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={onGenerate}
            groups={generation?.groups}
            courses={generation?.courses}
            placeholder={EXAMPLE}
          />
        </div>
        <div className="field credits">
          <span>Credits</span>
          <div className="credit-range">
            <input type="number" min={0} max={30} inputMode="numeric"
                   value={creditMin} placeholder="any"
                   aria-label="minimum credits"
                   onChange={(e) => setCreditMin(e.target.value)} />
            <span className="quiet">to</span>
            <input type="number" min={0} max={30} inputMode="numeric"
                   value={creditMax} placeholder="any"
                   aria-label="maximum credits"
                   onChange={(e) => setCreditMax(e.target.value)} />
          </div>
        </div>
        <label>
          <span>Term</span>
          <select value={term} onChange={(e) => setTerm(e.target.value)}>
            {(meta?.terms ?? []).map((t) => (
              <option key={t} value={t}>{formatTerm(t)}</option>
            ))}
          </select>
        </label>
        <button type="button" className="primary" onClick={onGenerate}
                disabled={generating}>
          {generating ? "Loading sections…" : stale ? "Update schedules" : "Find schedules"}
        </button>
      </div>

      {apiError && <div className="error">{apiError}</div>}

      {generation && (
        <div className="summary">
          {stale && (
            <span className="chip stale">
              Courses or credits changed. Results below are for your last search.
            </span>
          )}
          {generation.missing.map((code) => (
            <span key={code} className="chip missing">{code} not found</span>
          ))}
          {generation.skipped.map((label) => (
            <span key={label} className="chip missing"
                  title="No registrable set of sections, so it was left out">
              {label} can't be scheduled
            </span>
          ))}
          {generation.course_sets > 1 && (
            <span className="chip quiet">
              {generation.course_sets.toLocaleString()} course combinations
            </span>
          )}
          <span className="chip quiet">
            {generation.total_schedules.toLocaleString()} conflict-free
            {results && <> · {results.distinct_weeks} distinct weeks</>}
          </span>
        </div>
      )}

      {generation && meta && (
        <main>
          <aside>
            <Controls
              factors={meta.factors}
              courses={generation.courses}
              campuses={generation.campuses}
              preferredDays={preferredDays}
              timeStart={timeStart}
              timeEnd={timeEnd}
              tiers={tiers}
              creditTarget={creditTarget}
              required={required}
              commuteMinutes={commuteMinutes}
              onDaysChange={setPreferredDays}
              onTimeChange={(s, e) => { setTimeStart(s); setTimeEnd(e); }}
              onTiersChange={setTiers}
              onCreditTargetChange={setCreditTarget}
              onRequiredChange={setRequired}
              onCommuteChange={setCommuteMinutes}
            />
          </aside>

          <section className="results">
            {results?.compromises.length ? (
              <div className="warn">
                Nothing satisfies everything. Showing the closest fit;
                unmet: {results.compromises.join(", ")}.
              </div>
            ) : null}

            {results && results.alternatives.length === 0 && (
              <div className="warn">
                No schedule satisfies those requirements. Try relaxing a
                required professor or the travel buffer.
              </div>
            )}

            {current && (
              <>
                <div className={ranking ? "week stale" : "week"}>
                  <WeekGrid sections={current.sections} />
                </div>

                <table className="sections">
                  <thead>
                    <tr>
                      <th>Course</th><th>CRN</th><th>Type</th>
                      <th>When</th><th>Instructor</th><th>Campus</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.sections.map((section) => (
                      <tr key={section.section_id}>
                        <td>{section.course_label}</td>
                        <td className="mono">{section.section_id}</td>
                        <td>{section.kind.toLowerCase()}</td>
                        <td>
                          {section.meetings.length === 0 ? "—" :
                            section.meetings.map((m) =>
                              `${m.day_name.slice(0, 3)} ${m.start_label}–${m.end_label}`
                            ).join(", ")}
                        </td>
                        <td>
                          {section.instructor ?? <span className="quiet">not listed</span>}
                          {section.rating != null &&
                            <em className="rating">{section.rating.toFixed(1)}</em>}
                        </td>
                        <td className="quiet">{section.campus ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {current.explanation && (
                  <p className="explanation">{current.explanation}</p>
                )}
              </>
            )}
          </section>

          <aside className="alt-panel">
            <h3>
              Alternatives
              {ranking && <span className="spinner" aria-label="ranking" />}
            </h3>
            {results && (
              <AlternativeList
                alternatives={results.alternatives}
                fixedCourses={fixedCourses}
                selected={current?.rank ?? 1}
                onSelect={setSelected}
              />
            )}
          </aside>
        </main>
      )}
    </div>
  );
}
