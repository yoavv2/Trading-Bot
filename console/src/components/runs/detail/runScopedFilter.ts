/**
 * `GET /api/v1/operations/risk-events` has no `run_id` query param and caps its
 * response at `limit` <= 100 (the backend's `MAX_LIMIT`). Single-run views must
 * fetch strategy-wide (`limit=100`) and filter to the run client-side — which means
 * a run whose matching rows fall outside the 100 most-recent for the strategy will
 * silently lose rows unless the cap is detected and surfaced (see CappedDisclosure).
 */
export const OPERATIONS_MAX_LIMIT = 100;

/**
 * Filters `items` down to those belonging to `runId`. Pure — no I/O, no ordering
 * changes, safe to unit test without a DOM/network environment.
 */
export function filterByRun<T extends { run_id: string }>(
  items: T[],
  runId: string,
): T[] {
  return items.filter((item) => item.run_id === runId);
}

/**
 * True when the raw (pre-filter) row count returned by the API hit its max —
 * i.e. there may be additional matching rows for this run that didn't fit in the
 * 100-row window and were dropped before the client ever saw them. Must be
 * computed from the RAW count, never the post-filter count, so the disclosure
 * still fires when a run's filtered result is empty (an old run outside the
 * 100-row window) instead of being mistaken for "this run had none."
 */
export function isCapped(rawCount: number): boolean {
  return rawCount >= OPERATIONS_MAX_LIMIT;
}
