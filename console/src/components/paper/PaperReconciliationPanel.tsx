import type { ExecutionFinding, Reconciliation } from "./types";

type PaperReconciliationPanelProps = {
  reconciliation: Reconciliation | null;
  findings: ExecutionFinding[];
};

/**
 * Presentational reconciliation panel (PAPR-03). Receives already-fetched
 * data from PaperAnalyticsSection — no fetch, no StatusPanel/runs-detail
 * imports. A null reconciliation is the primary path today (Alpaca paper
 * credentials are not configured per STATE.md), so it renders an explicit
 * empty state.
 *
 * HONESTY NOTE: `findings` is the strategy-wide, most-recent execution
 * findings list — NOT the findings belonging to this specific reconciliation
 * run. The heading below states that scope explicitly; `finding_count` on
 * the reconciliation summary is the separate, run-scoped count.
 */
export function PaperReconciliationPanel({
  reconciliation,
  findings,
}: PaperReconciliationPanelProps) {
  if (reconciliation === null) {
    return (
      <p className="text-sm text-zinc-500">
        No reconciliation has been recorded yet.
      </p>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={
            reconciliation.blocks_execution
              ? "rounded border border-red-700 bg-red-950/60 px-2 py-0.5 text-xs font-bold tracking-wide text-red-300"
              : "rounded border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-xs font-bold tracking-wide text-zinc-400"
          }
        >
          {reconciliation.blocks_execution
            ? "BLOCKS EXECUTION"
            : "does not block execution"}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <dt className="text-zinc-500">Status</dt>
        <dd className="text-zinc-100">{reconciliation.status}</dd>

        <dt className="text-zinc-500">As-of session</dt>
        <dd className="text-zinc-300">
          {reconciliation.as_of_session ?? "—"}
        </dd>

        <dt className="text-zinc-500">Finding count</dt>
        <dd className="text-zinc-300">{reconciliation.finding_count}</dd>

        <dt className="text-zinc-500">Blocking count</dt>
        <dd className="text-zinc-300">{reconciliation.blocking_count}</dd>

        <dt className="text-zinc-500">Completed at</dt>
        <dd className="text-zinc-300">
          {reconciliation.completed_at ?? "—"}
        </dd>
      </dl>

      <section className="mt-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Recent execution findings (strategy-wide, most-recent)
        </h3>
        {findings.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-500">
            No recent execution findings.
          </p>
        ) : (
          <table className="mt-2 w-full text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-1 pr-2 font-normal">Event at</th>
                <th className="py-1 pr-2 font-normal">Event type</th>
                <th className="py-1 pr-2 font-normal">Severity</th>
                <th className="py-1 pr-2 font-normal">Message</th>
                <th className="py-1 pr-2 font-normal">Blocks execution</th>
                <th className="py-1 font-normal">Details</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((finding, index) => (
                <tr
                  key={`${finding.event_at}-${index}`}
                  className={
                    finding.blocks_execution
                      ? "border-t border-zinc-800 bg-red-950/30 text-red-100"
                      : "border-t border-zinc-800 text-zinc-300"
                  }
                >
                  <td className="py-1 pr-2">{finding.event_at}</td>
                  <td className="py-1 pr-2">{finding.event_type}</td>
                  <td className="py-1 pr-2">{finding.severity}</td>
                  <td className="py-1 pr-2">{finding.message}</td>
                  <td className="py-1 pr-2">
                    {finding.blocks_execution ? "yes" : "no"}
                  </td>
                  <td className="py-1 font-mono text-[11px]">
                    {finding.details !== undefined &&
                    finding.details !== null &&
                    !(
                      typeof finding.details === "object" &&
                      Object.keys(finding.details as object).length === 0
                    )
                      ? JSON.stringify(finding.details)
                      : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
