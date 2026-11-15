import type { CourseEntry, CourseGroup, Course } from "../types";

interface Props {
  entries: CourseEntry[];
  onChange: (entries: CourseEntry[]) => void;
  // The half-typed text is owned by the caller, not this component: clicking
  // "Find schedules" right after typing blurs the input first, and a chip
  // committed on blur would land in state *after* the search had already read
  // the old list. The caller folds the draft in with `withDraft` instead.
  draft: string;
  onDraftChange: (draft: string) => void;
  onSubmit: () => void;
  // From the last search, so a chip can say what it matched. Absent before
  // the first search; may describe an older request until the next one.
  groups?: CourseGroup[];
  courses?: Course[];
  placeholder: string;
}

/** "csci-ua 4XX " -> "CSCI-UA 4xx": the shape the API reports codes back in,
 *  so chips and the server's groups can be matched by string. */
export function normalizeCode(text: string): string {
  const upper = text.trim().toUpperCase().replace(/\s+/g, " ");
  const [subject, number] = upper.split(" ");
  return number ? `${subject} ${number.replace(/X/g, "x")}` : upper;
}

const isWildcard = (code: string) => /\s\d*x/i.test(code);

/** `entries` plus any new codes in `text` (comma-separated), as required
 *  entries taking one. Codes already present are not added twice. */
export function withDraft(entries: CourseEntry[], text: string): CourseEntry[] {
  const known = new Set(entries.map((e) => e.code));
  const fresh = text.split(",").map(normalizeCode).filter(Boolean)
    .filter((code, i, all) => !known.has(code) && all.indexOf(code) === i);
  return fresh.length
    ? [...entries, ...fresh.map((code) => ({ code, required: true, count: 1 }))]
    : entries;
}

/**
 * Courses as chips, each with its own Required/Optional toggle.
 *
 * A wildcard ('CSCI-UA 4xx'), or a code a search has shown to be several
 * courses (CSCI-UA 480's topics), also gets a "take N" stepper: the count is
 * part of what was asked for, so it lives on the chip rather than somewhere
 * else on the page.
 */
export function CourseInput({ entries, onChange, draft, onDraftChange, onSubmit,
                              groups, courses, placeholder }: Props) {
  const commit = (text: string) => {
    const next = withDraft(entries, text);
    if (next !== entries) onChange(next);
    onDraftChange("");
  };

  const update = (index: number, patch: Partial<CourseEntry>) =>
    onChange(entries.map((e, i) => (i === index ? { ...e, ...patch } : e)));

  const groupOf = (code: string) => groups?.find((g) => g.code === code);

  return (
    <div className="course-input" onClick={(e) =>
      (e.currentTarget.querySelector("input") as HTMLInputElement | null)?.focus()}>
      {entries.map((entry, index) => {
        const group = groupOf(entry.code);
        const matches = group?.matches.length;
        const several = isWildcard(entry.code) || (matches ?? 0) > 1;
        const credits = several ? null
          : courses?.find((c) => c.code === entry.code && !c.topic)?.credits ?? null;
        return (
          <span key={entry.code}
                className={entry.required ? "course-chip" : "course-chip optional"}>
            <span className="course-chip-code">{entry.code}</span>
            {several && matches !== undefined && (
              <em>{matches} course{matches === 1 ? "" : "s"}</em>)}
            {credits !== null && <em>{credits} cr</em>}

            {several && (
              <span className="stepper" title="How many to take from this group">
                <span className="quiet">take</span>
                <button type="button" aria-label="take fewer"
                        disabled={entry.count <= 1}
                        onClick={() => update(index, { count: entry.count - 1 })}>−</button>
                <span className="stepper-value">{entry.count}</span>
                <button type="button" aria-label="take more"
                        disabled={matches !== undefined && entry.count >= matches}
                        onClick={() => update(index, { count: entry.count + 1 })}>+</button>
              </span>
            )}

            <button type="button" className="toggle"
                    title={entry.required
                      ? "Required: every schedule includes it. Click to make optional."
                      : "Optional: may be left out. Click to make required."}
                    onClick={() => update(index, { required: !entry.required })}>
              {entry.required ? "Required" : "Optional"}
            </button>
            <button type="button" className="remove" aria-label={`remove ${entry.code}`}
                    onClick={() => onChange(entries.filter((_, i) => i !== index))}>
              ×
            </button>
          </span>
        );
      })}
      <input
        value={draft}
        placeholder={entries.length ? "Add a course or wildcard…" : placeholder}
        onChange={(e) => {
          // A typed or pasted comma finishes a chip.
          if (e.target.value.includes(",")) commit(e.target.value);
          else onDraftChange(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            if (draft.trim()) commit(draft);
            else onSubmit();
          } else if (e.key === "Backspace" && !draft && entries.length) {
            onChange(entries.slice(0, -1));
          }
        }}
      />
    </div>
  );
}
