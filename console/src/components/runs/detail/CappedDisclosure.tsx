type CappedDisclosureProps = {
  /** True when the raw (pre-filter) API response hit OPERATIONS_MAX_LIMIT. */
  capped: boolean;
  /** Plural noun for the row type, e.g. "risk events", "signals". */
  rowNoun: string;
  /** Count of rows matched for this run AFTER filtering — may be 0. */
  matchedCount: number;
};

/**
 * Amber truncation-disclosure banner, shared by every run-detail section that
 * fetches strategy-wide and filters client-side (see runScopedFilter.ts). Renders
 * ONLY when `capped` is true — including when `matchedCount` is 0, which is the
 * "old run outside the 100-row window" case that must read as "may be truncated,"
 * never as "this run had none." Visually distinct (amber/warning) from the red
 * ErrorState so operators don't confuse a truncation caveat with a fetch failure.
 */
export function CappedDisclosure({
  capped,
  rowNoun,
  matchedCount,
}: CappedDisclosureProps) {
  if (!capped) {
    return null;
  }

  return (
    <div className="rounded border border-amber-800 bg-amber-950/40 px-4 py-3 text-sm text-amber-100">
      <p>
        Showing {matchedCount} {rowNoun} matched for this run. The API
        returned its maximum of 100 most-recent {rowNoun} for this strategy,
        so older {rowNoun} for this run may not appear here.
      </p>
    </div>
  );
}
