"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { CappedDisclosure } from "./CappedDisclosure";
import { filterByRun, isCapped } from "./runScopedFilter";

export type RiskEventItem = {
  risk_event_id: string;
  run_id: string;
  strategy_id: string;
  run_type: string;
  run_status: string;
  symbol: string;
  session_date: string;
  signal_direction: string | null;
  signal_reason: string | null;
  outcome: string;
  decision_code: string | null;
  decision_reason: string | null;
  reference_price: string | null;
  proposed_quantity: string | null;
  proposed_notional: string | null;
  risk_metadata: Record<string, unknown> | null;
};

type RiskEventsResponse = {
  filters: Record<string, unknown>;
  count: number;
  items: RiskEventItem[];
};

type SignalsRiskPanelProps = {
  runId: string;
  strategyId: string;
};

const BLOCKED_OUTCOME_NEEDLES = ["block", "reject"];

function isBlockedOutcome(outcome: string): boolean {
  const lower = outcome.toLowerCase();
  return BLOCKED_OUTCOME_NEEDLES.some((needle) => lower.includes(needle));
}

/**
 * RUNS-03/RUNS-04: this run's signals (direction + reason) and risk decisions
 * (incl. blocked trades with human-readable reasons). `/api/v1/operations/risk-events`
 * has no run_id filter, so this fetches strategy-wide (limit=100) and filters
 * client-side via filterByRun — surfacing CappedDisclosure whenever the RAW
 * response hit the 100-row cap, even when the filtered result is empty (an old
 * run outside the 100-row window must read "may be truncated," not "no signals").
 */
export function SignalsRiskPanel({ runId, strategyId }: SignalsRiskPanelProps) {
  const endpoint = `/api/v1/operations/risk-events?strategy_id=${strategyId}&limit=100`;
  const { loading, result, refetch } = useApiQuery<RiskEventsResponse>(endpoint);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">
          Signals & Risk Decisions
        </h2>
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !result.ok ? (
          <ErrorState failure={result} title="Failed to load risk events" />
        ) : (
          <SignalsRiskContent data={result.data} runId={runId} />
        )}
      </div>
    </section>
  );
}

function SignalsRiskContent({
  data,
  runId,
}: {
  data: RiskEventsResponse;
  runId: string;
}) {
  const matched = filterByRun(data.items, runId);
  const capped = isCapped(data.count);

  return (
    <div className="space-y-4">
      <CappedDisclosure
        capped={capped}
        rowNoun="risk events"
        matchedCount={matched.length}
      />

      {matched.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {capped
            ? "No matching rows in the 100 most-recent risk events."
            : "No risk events recorded for this run."}
        </p>
      ) : (
        <>
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase text-zinc-400">
              Signals
            </h3>
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-zinc-500">
                  <th className="pb-1 pr-2">Symbol</th>
                  <th className="pb-1 pr-2">Session</th>
                  <th className="pb-1 pr-2">Direction</th>
                  <th className="pb-1">Reason</th>
                </tr>
              </thead>
              <tbody>
                {matched.map((item) => (
                  <tr
                    key={item.risk_event_id}
                    className="border-t border-zinc-800"
                  >
                    <td className="py-1 pr-2 text-zinc-200">{item.symbol}</td>
                    <td className="py-1 pr-2 text-zinc-300">
                      {item.session_date}
                    </td>
                    <td className="py-1 pr-2 text-zinc-300">
                      {item.signal_direction ?? "—"}
                    </td>
                    <td className="py-1 text-zinc-300">
                      {item.signal_reason ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase text-zinc-400">
              Risk Decisions
            </h3>
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-zinc-500">
                  <th className="pb-1 pr-2">Symbol</th>
                  <th className="pb-1 pr-2">Outcome</th>
                  <th className="pb-1 pr-2">Code</th>
                  <th className="pb-1">Reason</th>
                </tr>
              </thead>
              <tbody>
                {matched.map((item) => {
                  const blocked = isBlockedOutcome(item.outcome);
                  return (
                    <tr
                      key={item.risk_event_id}
                      className="border-t border-zinc-800"
                    >
                      <td className="py-1 pr-2 text-zinc-200">
                        {item.symbol}
                      </td>
                      <td
                        className={`py-1 pr-2 font-semibold uppercase ${
                          blocked ? "text-red-400" : "text-zinc-300"
                        }`}
                      >
                        {item.outcome}
                      </td>
                      <td className="py-1 pr-2 text-zinc-300">
                        {item.decision_code ?? "—"}
                      </td>
                      <td
                        className={
                          blocked
                            ? "py-1 font-medium text-red-300"
                            : "py-1 text-zinc-300"
                        }
                      >
                        {item.decision_reason ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
