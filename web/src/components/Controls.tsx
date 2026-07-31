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
  required: Record<string, string>;
  commuteMinutes: number;
  onDaysChange: (days: string[]) => void;
  onTimeChange: (start: string, end: string) => void;
  onTiersChange: (tiers: string[][]) => void;
  onRequiredChange: (required: Record<string, string>) => void;
  onCommuteChange: (minutes: number) => void;
}

export function Controls(props: Props) {
  const {
    factors, courses, campuses, preferredDays, timeStart, timeEnd, tiers,
    required, commuteMinutes,
  } = props;

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
        <h3>Non-negotiable professors</h3>
        <p className="hint">
          A requirement filters before ranking. A weight never can &mdash; it only
          makes a match score well, so it can still lose on other factors.
        </p>
        {courses.map((course) => (
          <label key={course.code} className="require">
            <span>{course.code}</span>
            <select
              value={required[course.code] ?? ""}
              onChange={(e) => {
                const next = { ...required };
                if (e.target.value) next[course.code] = e.target.value;
                else delete next[course.code];
                props.onRequiredChange(next);
              }}
              disabled={course.instructors.length === 0}
            >
              <option value="">
                {course.instructors.length ? "anyone" : "no instructors published"}
              </option>
              {course.instructors.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </label>
        ))}
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
