// Display-boundary text formatting. Backend strings (factor labels, alternative
// reasons) stay lowercase on purpose -- they're also embedded mid-sentence in
// narration ("best for days on campus") -- so capitalizing them for on-screen
// labels happens here, not in scoring.py.

/** "days on campus" -> "Days on campus" */
export function capitalize(text: string): string {
  return text.length ? text[0].toUpperCase() + text.slice(1) : text;
}

const SEASON_LABELS: Record<string, string> = {
  fall: "Fall", spring: "Spring", summer: "Summer", winter: "Winter",
};

/** "fall2026" -> "Fall 2026" */
export function formatTerm(term: string): string {
  const match = term.match(/^([a-z]+)(\d+)$/i);
  if (!match) return term;
  const [, season, year] = match;
  return `${SEASON_LABELS[season.toLowerCase()] ?? capitalize(season)} ${year}`;
}
