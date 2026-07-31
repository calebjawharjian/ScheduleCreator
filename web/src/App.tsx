import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchMetadata, generate, rank } from "./api";
import { AlternativeList } from "./components/AlternativeList";
import { Controls } from "./components/Controls";
import { WeekGrid } from "./components/WeekGrid";
import { formatTerm } from "./format";
import { defaultTiers, tiersToWeights } from "./weights";
import type {
  Alternative, GenerateResponse, Metadata, RankResponse,
} from "./types";

const EXAMPLE = "CSCI-UA 102, MATH-UA 121, EXPOS-UA 1, EG-UY 1004";

// An empty time window means "no restriction", not midnight-to-midnight as a
// literal preference -- so a blank picker (the default) and a full-day one
// score identically. Kept out of state so the inputs themselves can stay
// genuinely empty until the user sets a window.
const FULL_DAY_START = "00:00";
const FULL_DAY_END = "23:59";

export default function App() {
  const [meta, setMeta] = useState<Metadata | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const [term, setTerm] = useState("fall2026");
  const [courseText, setCourseText] = useState("");
  const [generation, setGeneration] = useState<GenerateResponse | null>(null);
  const [generating, setGenerating] = useState(false);

  const [preferredDays, setPreferredDays] = useState<string[]>([]);
  const [timeStart, setTimeStart] = useState("");
  const [timeEnd, setTimeEnd] = useState("");
  const [tiers, setTiers] = useState<string[][]>([]);
  const weights = useMemo(() => tiersToWeights(tiers), [tiers]);
  const [required, setRequired] = useState<Record<string, string>>({});
  const [commuteMinutes, setCommuteMinutes] = useState(30);

  const [results, setResults] = useState<RankResponse | null>(null);
  const [selected, setSelected] = useState(1);
  const [ranking, setRanking] = useState(false);

  useEffect(() => {
    fetchMetadata()
      .then((m) => {
        setMeta(m);
        setTiers(defaultTiers(m.factors.map((f) => f.name)));
      })
      .catch(() => setApiError(
        "Cannot reach the API. Start it with: ./venv/bin/python -m uvicorn api:app"));
  }, []);

  const onGenerate = async () => {
    const courses = courseText.split(",").map((c) => c.trim()).filter(Boolean);
    if (courses.length === 0) return;
    setGenerating(true);
    setApiError(null);
    setResults(null);
    try {
      const body = await generate(term, courses);
      setGeneration(body);
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
  }, [generation, preferredDays, timeStart, timeEnd, weights, required,
      commuteMinutes]);

  useEffect(() => {
    if (!generation) return;
    const timer = setTimeout(runRank, 180);
    return () => clearTimeout(timer);
  }, [generation, runRank]);

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
        <label className="grow">
          <span>Courses</span>
          <input
            value={courseText}
            placeholder={EXAMPLE}
            onChange={(e) => setCourseText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onGenerate()}
          />
        </label>
        <label>
          <span>Term</span>
          <select value={term} onChange={(e) => setTerm(e.target.value)}>
            {(meta?.terms ?? ["fall2026"]).map((t) => (
              <option key={t} value={t}>{formatTerm(t)}</option>
            ))}
          </select>
        </label>
        <button type="button" className="primary" onClick={onGenerate}
                disabled={generating}>
          {generating ? "Loading sections…" : "Find schedules"}
        </button>
      </div>

      {apiError && <div className="error">{apiError}</div>}

      {generation && (
        <div className="summary">
          {generation.courses.map((course) => (
            <span key={course.code} className="chip">
              {course.code}
              <em>{course.sections} sections</em>
            </span>
          ))}
          {generation.missing.map((code) => (
            <span key={code} className="chip missing">{code} not found</span>
          ))}
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
              required={required}
              commuteMinutes={commuteMinutes}
              onDaysChange={setPreferredDays}
              onTimeChange={(s, e) => { setTimeStart(s); setTimeEnd(e); }}
              onTiersChange={setTiers}
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
                        <td>{section.course_code}</td>
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
