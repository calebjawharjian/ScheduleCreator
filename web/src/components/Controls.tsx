import { useState } from "react";
import type { Course, FactorMeta } from "../types";
import { WeightRanking } from "./WeightRanking";

const DAYS = ["MON", "TUE", "WED", "THU", "FRI"];

interface Props {
  factors: FactorMeta[];
  courses: Course[];
  campuses: string[];
  preferredDays: string[];
  timeStart: string;
  timeEnd: string;
  tiers: string[][];
  creditTarget: number;
  required: Record<string, string>;
  commuteMinutes: number;
  onDaysChange: (days: string[]) => void;
  onTimeChange: (start: string, end: string) => void;
  onTiersChange: (tiers: string[][]) => void;
  onCreditTargetChange: (credits: number) => void;
  onRequiredChange: (required: Record<string, string>) => void;
  onCommuteChange: (minutes: number) => void;
}

export function Controls(props: Props) {
  const {
    factors, courses, campuses, preferredDays, timeStart, timeEnd, tiers,
    creditTarget, required, commuteMinutes,
  } = props;
  const [targetText, setTargetText] = useState(String(creditTarget));

  // One row per catalog code. A requirement is keyed by code in the API, and
  // CSCI-UA 480's topics share one -- so their instructors are merged here
  // rather than listing seven rows that would all set the same requirement.
  const instructorsByCode = new Map<string, Set<string>>();
  for (const course of courses) {
    const names = instructorsByCode.get(course.code) ?? new Set<string>();
    course.instructors.forEach((name) => names.add(name));
    instructorsByCode.set(course.code, names);
  }

  const toggleDay = (day: string) => {
    props.onDaysChange(
      preferredDays.includes(day)
        ? preferredDays.filter((d) => d !== day)
        : [...preferredDays, day]);
  };

  return (
    <div className="controls">
      <section>
        <h3>Preferred days</h3>
        <div className="day-toggles">
          {DAYS.map((day) => (
            <button
              key={day}
              type="button"
              className={preferredDays.includes(day) ? "day on" : "day"}
              onClick={() => toggleDay(day)}
            >
              {day[0] + day.slice(1, 3).toLowerCase()}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3>Time window</h3>
        <div className="row">
          <label>
            <span>From</span>
            <input type="time" value={timeStart}
                   onChange={(e) => props.onTimeChange(e.target.value, timeEnd)} />
          </label>
          <label>
            <span>To</span>
            <input type="time" value={timeEnd}
                   onChange={(e) => props.onTimeChange(timeStart, e.target.value)} />
          </label>
        </div>
      </section>

      <section>
        <h3>What matters to you</h3>
        <p className="hint">
          Drag a factor to rank it. Drop one onto another to tie them at the
          same priority.
        </p>
        <WeightRanking factors={factors} tiers={tiers} onChange={props.onTiersChange} />
      </section>

      <section>
        <h3>Credit load</h3>
        <p className="hint">
          The total the credit-load factor aims for. Each 4-credit course away
          from it costs half that factor's score.
        </p>
        <label className="target">
          <span>Aim for</span>
          <input type="number" min={0} max={30} value={targetText}
                 onChange={(e) => {
                   // The box keeps its own text so it can be emptied mid-edit;
                   // only a real number reaches the ranking, never a blank as 0.
                   setTargetText(e.target.value);
                   const value = Number(e.target.value);
                   if (e.target.value !== "" && value >= 0) {
                     props.onCreditTargetChange(value);
                   }
                 }}
                 onBlur={() => setTargetText(String(creditTarget))} />
          <span>credits</span>
        </label>
      </section>

      <section>
        <h3>Non-negotiable professors</h3>
        <p className="hint">
          A requirement filters before ranking. A weight never can &mdash; it only
          makes a match score well, so it can still lose on other factors. For an
          optional course it applies only to schedules that include it.
        </p>
        {(() => {
          // A wildcard can bring 17 candidates, which turned this into a wall
          // of dropdowns that pushed everything below it off screen. Courses
          // with no published names get one line instead of a dead dropdown,
          // and a long list folds -- staying open if a requirement is set, so
          // an active filter is never hidden.
          const named = [...instructorsByCode].filter(([, n]) => n.size > 0);
          const unnamed = [...instructorsByCode].filter(([, n]) => n.size === 0)
            .map(([code]) => code);
          const rows = named.map(([code, names]) => (
            <label key={code} className="require">
              <span>{code}</span>
              <select
                value={required[code] ?? ""}
                onChange={(e) => {
                  const next = { ...required };
                  if (e.target.value) next[code] = e.target.value;
                  else delete next[code];
                  props.onRequiredChange(next);
                }}
              >
                <option value="">anyone</option>
                {[...names].sort().map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
          ));
          return (
            <>
              {rows.length > 5 ? (
                <details className="require-list"
                         open={Object.keys(required).length > 0}>
                  <summary>
                    {rows.length} courses with published instructors
                    {Object.keys(required).length > 0 &&
                      ` · ${Object.keys(required).length} required`}
                  </summary>
                  {rows}
                </details>
              ) : rows}
              {unnamed.length > 0 && (
                <p className="hint">
                  No instructors published yet for {unnamed.join(", ")}.
                </p>
              )}
            </>
          );
        })()}
      </section>

      {campuses.length > 1 && (
        <section>
          <h3>Travel between campuses</h3>
          <p className="hint">
            Your courses span {campuses.join(" and ")}. Back-to-back classes
            across them cannot be attended, even though they do not overlap.
          </p>
          <label className="slider">
            <span className="slider-label">
              Minutes required
              <em>{commuteMinutes === 0 ? "ignore" : commuteMinutes}</em>
            </span>
            <input
              type="range" min={0} max={60} step={5} value={commuteMinutes}
              onChange={(e) => props.onCommuteChange(Number(e.target.value))}
            />
          </label>
        </section>
      )}
    </div>
  );
}
