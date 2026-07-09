"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { EquityCurveChart, type EquityPoint } from "./EquityCurveChart";
import { SummaryMetricsPanel } from "./SummaryMetricsPanel";

export type AnalyticsBacktestBlock = {
  run_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  summary: Record<string, unknown>;
  metrics: Record<string, unknown>;
  equity_curve: EquityPoint[];
};

export type AnalyticsResponse = {
  strategy: Record<string, unknown>;
  backtest: AnalyticsBacktestBlock | null;
  paper: unknown;
};

type BacktestAnalyticsSectionProps = {
  runId: string;
  strategyId: string;
};

/**
 * ANLX-01/ANLX-02: single-fetch owner for the backtest analytics view
 * (equity curve chart + curated summary-metrics panel), mirroring the
 * 14-03/15-01 single-owner-fetch precedent. Deliberately independent of
 * MetricsPanel.tsx (RUNS-06's full raw metrics table) — that redundant-
 * looking second fetch of the same endpoint is intentional per 16-CONTEXT;
 * this section owns its own fetch rather than reusing/editing MetricsPanel.
 */
export function BacktestAnalyticsSection({
  runId,
  strategyId,
}: BacktestAnalyticsSectionProps) {
  const endpoint = `/api/v1/analytics/strategies/${strategyId}?backtest_run_id=${runId}`;
  const { loading, result, refetch } = useApiQuery<AnalyticsResponse>(endpoint);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Analytics</h2>
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>
      <div className="mt-3 space-y-4">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !result.ok ? (
          <ErrorState failure={result} title="Failed to load analytics" />
        ) : (
          (() => {
            const backtest = result.data.backtest;
            return (
              <>
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Equity curve
                  </h3>
                  <EquityCurveChart points={backtest?.equity_curve} />
                </div>
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Summary
                  </h3>
                  <SummaryMetricsPanel metrics={backtest?.metrics} />
                </div>
              </>
            );
          })()
        )}
      </div>
    </section>
  );
}
