import type { Alternative } from "../types";

interface Props {
  alternatives: Alternative[];
  selected: number;
  onSelect: (rank: number) => void;
}

/**
 * The ranked list, with each row carrying the reason it is on screen.
 *
 * "best for days on campus" is not decoration: those rows are reserved by
 * `diverse_top` for weeks that are structurally different, which is the only
 * reason a three-day week appears at all when it scores below dozens of
 * five-day ones.
 */
export function AlternativeList({ alternatives, selected, onSelect }: Props) {
  return (
    <ol className="alternatives">
      {alternatives.map((alternative) => {
        const reserved = alternative.factor !== null;
        return (
          <li key={alternative.rank}>
            <button
              type="button"
              className={alternative.rank === selected ? "alt selected" : "alt"}
              onClick={() => onSelect(alternative.rank)}
            >
              <span className="alt-head">
                <span className={reserved ? "alt-reason reserved" : "alt-reason"}>
                  {alternative.reason}
                </span>
                <span className="alt-score">{alternative.score.toFixed(2)}</span>
              </span>

              <span className="alt-meta">
                {alternative.days_on_campus} day
                {alternative.days_on_campus === 1 ? "" : "s"} on campus
                {alternative.variants > 1 && (
                  <> · {alternative.variants - 1} identical alternative
                    {alternative.variants > 2 ? "s" : ""}</>
                )}
              </span>

              <span className="bars">
                {alternative.factors.map((factor) => (
                  <span key={factor.name} className="bar" title={
                    `${factor.label}: ${(factor.score * 100).toFixed(0)}%`}>
                    <span className="bar-fill"
                          style={{ height: `${Math.max(factor.score * 100, 2)}%` }} />
                  </span>
                ))}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
