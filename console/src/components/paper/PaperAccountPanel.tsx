import type { AccountSnapshot } from "./types";

type PaperAccountPanelProps = {
  snapshot: AccountSnapshot | null;
};

/**
 * Presentational account-snapshot panel (PAPR-04). Receives already-fetched
 * data from PaperAnalyticsSection — no fetch, no StatusPanel/runs-detail
 * imports. A null snapshot is the primary path today (Alpaca paper
 * credentials are not configured per STATE.md), so it renders an explicit
 * empty state rather than a fabricated zeros card.
 */
export function PaperAccountPanel({ snapshot }: PaperAccountPanelProps) {
  if (snapshot === null) {
    return (
      <p className="text-sm text-zinc-500">No account snapshot recorded yet.</p>
    );
  }

  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
      <dt className="text-zinc-500">Total equity</dt>
      <dd className="font-mono text-zinc-100">
        {snapshot.total_equity.toLocaleString()}
      </dd>

      <dt className="text-zinc-500">Cash</dt>
      <dd className="font-mono text-zinc-100">
        {snapshot.cash.toLocaleString()}
      </dd>

      <dt className="text-zinc-500">Buying power</dt>
      <dd className="font-mono text-zinc-100">
        {snapshot.buying_power.toLocaleString()}
      </dd>

      <dt className="text-zinc-500">Gross exposure</dt>
      <dd className="font-mono text-zinc-300">
        {snapshot.gross_exposure.toLocaleString()}
      </dd>

      <dt className="text-zinc-500">Open positions</dt>
      <dd className="text-zinc-300">{snapshot.open_positions}</dd>

      <dt className="text-zinc-500">Snapshot source</dt>
      <dd className="text-zinc-300">{snapshot.snapshot_source}</dd>

      <dt className="text-zinc-500">Snapshot at</dt>
      <dd className="text-zinc-300">{snapshot.snapshot_at}</dd>
    </dl>
  );
}
