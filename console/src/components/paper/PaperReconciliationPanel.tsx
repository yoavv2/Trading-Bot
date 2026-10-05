import type { ReactNode } from "react";
import { tradingBlockerLabel } from "@/lib/useActivePaperStrategy";
import type { ExecutionFinding, Reconciliation } from "./types";

type PaperReconciliationPanelProps = {
  reconciliation: Reconciliation | null;
  findings: ExecutionFinding[];
  /**
   * 20.1-14: the closed trading-blocked reasons; `null` while they are unknown (loading or a
   * failed fetch) -- then the panel never shows the reassuring "does not block execution".
   */
  tradingBlockedReasons: string[] | null;
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
  tradingBlockedReasons,
}: PaperReconciliationPanelProps) {
  const blockedReasons = tradingBlockedReasons ?? [];
  if (reconciliation === null) {
    return (
      <div>
        <p className="text-sm text-zinc-500">
          No reconciliation has been recorded yet.
        </p>
        {blockedReasons.map((reason) => (
          <p key={reason} className="mt-1 text-sm text-red-300">
            {`Trading blocked: ${tradingBlockerLabel(reason)}`}
          </p>
        ))}
      </div>
    );
  }

  const BADGE_BASE = "rounded border px-2 py-0.5 text-xs font-bold tracking-wide";
  // Exactly one badge group: the run blocks; else trading is blocked for another reason; else
  // the blockers are unknown; else (known and empty) the run does not block execution.
  let badges: ReactNode;
  if (reconciliation.blocks_execution) {
    badges = (
      <span className={`${BADGE_BASE} border-red-700 bg-red-950/60 text-red-300`}>
        BLOCKS EXECUTION
      </span>
    );
  } else if (tradingBlockedReasons === null) {
    badges = (
      <span className={`${BADGE_BASE} border-amber-700 bg-amber-950/40 text-amber-300`}>
        Trading permission unknown
      </span>
    );
  } else if (blockedReasons.length > 0) {
    badges = blockedReasons.map((reason) => (
      <span
        key={reason}
        className={`${BADGE_BASE} border-red-700 bg-red-950/60 text-red-300`}
      >
        {`TRADING BLOCKED: ${tradingBlockerLabel(reason)}`}
      </span>
    ));
  } else {
    badges = (
      <span className={`${BADGE_BASE} border-zinc-700 bg-zinc-800/60 text-zinc-400`}>
        does not block execution
      </span>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">{badges}</div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <dt className="text-zinc-500">Scope</dt>
        <dd className="text-zinc-300">{reconciliation.scope ?? "—"}</dd>

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
