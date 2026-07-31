import type { Section } from "../types";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/**
 * A schedule drawn as the week it actually is.
 *
 * Blocks are positioned from `meeting.start`/`end` in minutes since midnight,
 * which is why the API sends those alongside the formatted labels: geometry
 * needs the number, the label needs the string, and deriving one from the
 * other in the browser is how clock times drift.
 */
export function WeekGrid({ sections }: { sections: Section[] }) {
  const placed = sections.flatMap((section) =>
    section.meetings.map((meeting) => ({ section, meeting })));

  if (placed.length === 0) {
    return <p className="empty">This schedule has no meeting times.</p>;
  }

  // Only show days that are actually used, so a Mon/Wed/Fri week does not
  // render two dead columns.
  const usedDays = [...new Set(placed.map((p) => p.meeting.day))].sort((a, b) => a - b);

  // Snap the visible range to whole hours around the real extent.
  const earliest = Math.min(...placed.map((p) => p.meeting.start));
  const latest = Math.max(...placed.map((p) => p.meeting.end));
  const from = Math.floor(earliest / 60) * 60;
  const to = Math.ceil(latest / 60) * 60;
  const span = Math.max(to - from, 60);

  const hours: number[] = [];
  for (let h = from; h <= to; h += 60) hours.push(h);

  // One stable hue per course, so the same course keeps its colour across
  // every alternative the user clicks through.
  const courses = [...new Set(sections.map((s) => s.course_code))].sort();
  const hueOf = (code: string) => (courses.indexOf(code) * 47) % 360;

  return (
    <div className="grid" style={{ ["--rows" as string]: span }}>
      <div className="grid-hours" aria-hidden="true">
        {hours.map((h) => (
          <div key={h} className="grid-hour" style={{ top: `${((h - from) / span) * 100}%` }}>
            <span>{String(Math.floor(h / 60)).padStart(2, "0")}:00</span>
          </div>
        ))}
      </div>

      <div className="grid-days">
        {usedDays.map((day) => (
          <div key={day} className="grid-day">
            <div className="grid-day-label">{DAY_NAMES[day]}</div>
            <div className="grid-day-body">
              {hours.slice(1).map((h) => (
                <div key={h} className="grid-line"
                     style={{ top: `${((h - from) / span) * 100}%` }} />
              ))}
              {placed
                .filter((p) => p.meeting.day === day)
                .map(({ section, meeting }) => {
                  const top = ((meeting.start - from) / span) * 100;
                  const height = ((meeting.end - meeting.start) / span) * 100;
                  const hue = hueOf(section.course_code);
                  return (
                    <div
                      key={`${section.section_id}-${meeting.day}-${meeting.start}`}
                      className="block"
                      style={{
                        top: `${top}%`,
                        height: `${height}%`,
                        ["--hue" as string]: hue,
                      }}
                      title={`${section.course_code} ${section.kind} · CRN ${section.section_id}`}
                    >
                      <span className="block-code">{section.course_code}</span>
                      <span className="block-kind">{section.kind.toLowerCase()}</span>
                      <span className="block-time">
                        {meeting.start_label}–{meeting.end_label}
                      </span>
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
