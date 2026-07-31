// A tier is a set of factor names the user has declared equally important;
// tiers are ordered most-important first. That's the whole model -- ties are
// just "same tier" -- so deriving weights is a straight linear ramp: each
// tier outweighs the one below it, and everyone inside a tier gets the same
// weight because score_schedule only ever uses weights in relative terms
// (see scoring.py). A single tier holding every factor means nothing has
// been prioritized over anything else yet, which is the honest default.

export function defaultTiers(factorNames: string[]): string[][] {
  return factorNames.length ? [factorNames] : [];
}

export function tiersToWeights(tiers: string[][]): Record<string, number> {
  const total = tiers.length;
  const weights: Record<string, number> = {};
  tiers.forEach((tier, index) => {
    const weight = total - index;
    for (const name of tier) weights[name] = weight;
  });
  return weights;
}

/** Removes `name` from wherever it sits, dropping any tier that empties out. */
function withoutFactor(tiers: string[][], name: string): {
  working: string[][]; originIndex: number; originEmptied: boolean;
} {
  const originIndex = tiers.findIndex((tier) => tier.includes(name));
  const working = tiers.map((tier) => tier.filter((n) => n !== name));
  const originEmptied = originIndex !== -1 && working[originIndex].length === 0;
  return { working: working.filter((tier) => tier.length > 0), originIndex, originEmptied };
}

/** Ties `name` into the tier currently at `tierIndex`. A no-op if it's already there. */
export function moveIntoTier(tiers: string[][], name: string, tierIndex: number): string[][] {
  if (tiers[tierIndex]?.includes(name)) return tiers;
  const { working, originIndex, originEmptied } = withoutFactor(tiers, name);
  const target = originEmptied && originIndex < tierIndex ? tierIndex - 1 : tierIndex;
  const next = working.map((tier) => tier.slice());
  next[target] = [...next[target], name];
  return next;
}

/** Pulls `name` out on its own, inserting a new single-factor tier at `gapIndex`
 *  (0 = above everything, tiers.length = below everything). */
export function moveIntoGap(tiers: string[][], name: string, gapIndex: number): string[][] {
  const { working, originIndex, originEmptied } = withoutFactor(tiers, name);
  const target = originEmptied && originIndex < gapIndex ? gapIndex - 1 : gapIndex;
  const next = working.map((tier) => tier.slice());
  next.splice(Math.max(0, Math.min(target, next.length)), 0, [name]);
  return next;
}
