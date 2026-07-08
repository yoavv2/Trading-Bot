"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";

export type BacktestMetricsBlock = {
  run_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  summary: Record<string, unknown>;
  metrics: Record<string, unknown>;
};

export type AnalyticsResponse = {
  strategy: Record<string, unknown>;
  backtest: BacktestMetricsBlock | null;
  paper: Record<string, unknown> | null;
};

type MetricsPanelProps = {
  runId: string;
  strategyId: string;
  runType: string;
};

const NO_METRICS_SURFACE_NOTE =
  "The analytics read-surface only exposes per-run metrics for backtest and paper-execution runs.";

/**
 * RUNS-06: this run's persisted metrics via the per-run analytics endpoint
 * (/api/v1/analytics/strategies/{strategyId}?backtest_run_id=... for backtest
 * runs, ?paper_run_id=... for paper_execution runs). That endpoint 400/404s
 * for any run_id that isn't a backtest or paper-execution run, so this panel
 * is run-type-aware: for risk_evaluation/reconciliation/dry_bootstrap/
 * operator_control/etc. runs it renders an honest static "no persisted
 * metrics for this run type" state and never mounts the fetching child --
 * conditionally rendering a child component (rather than conditionally
 * calling the hook inline) keeps every call to useApiQuery unconditional,
 * satisfying react-hooks/rules-of-hooks.
 */
export function MetricsPanel({ runId, strategyId, runType }: MetricsPanelProps) {
  const isBacktest = runType === "backtest";
  const isPaper = runType === "paper_execution";

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Metrics</h2>
      </div>
      <div className="mt-3">
        {isBacktest ? (
          <MetricsFetchContent
            strategyId={strategyId}
            runId={runId}
            queryParam="backtest_run_id"
            block="backtest"
          />
        ) : isPaper ? (
          <MetricsFetchContent
            strategyId={strategyId}
            runId={runId}
            queryParam="paper_run_id"
            block="paper"
          />
        ) : (
          <p className="text-sm text-zinc-500">
            No persisted per-run metrics for {runType} runs.{" "}
            {NO_METRICS_SURFACE_NOTE}
          </p>
        )}
      </div>
    </section>
  );
}

type MetricsFetchContentProps = {
  strategyId: string;
  runId: string;
  queryParam: "backtest_run_id" | "paper_run_id";
  block: "backtest" | "paper";
};

function MetricsFetchContent({
  strategyId,
  runId,
  queryParam,
  block,
}: MetricsFetchContentProps) {
  const endpoint = `/api/v1/analytics/strategies/${strategyId}?${queryParam}=${runId}`;
  const { loading, result, refetch } = useApiQuery<AnalyticsResponse>(endpoint);

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>
      {!result ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : !result.ok ? (
        <ErrorState failure={result} title="Failed to load metrics" />
      ) : (
        <MetricsBlockContent data={result.data} block={block} />
      )}
    </div>
  );
}

function MetricsBlockContent({
  data,
  block,
}: {
  data: AnalyticsResponse;
  block: "backtest" | "paper";
}) {
  if (block === "backtest") {
    const backtest = data.backtest;
    if (!backtest) {
      return (
        <p className="text-sm text-zinc-500">
          No backtest metrics found for this run.
        </p>
      );
    }
    return (
      <div className="space-y-3">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          <dt className="text-zinc-500">Status</dt>
          <dd className="text-zinc-300">{backtest.status}</dd>
          <dt className="text-zinc-500">Started</dt>
          <dd className="text-zinc-300">{backtest.started_at}</dd>
          <dt className="text-zinc-500">Completed</dt>
          <dd className="text-zinc-300">{backtest.completed_at ?? "—"}</dd>
        </dl>
        <MetricsEntries title="Summary" entries={backtest.summary} />
        <MetricsEntries title="Metrics" entries={backtest.metrics} />
      </div>
    );
  }

  const paper = data.paper;
  if (!paper) {
    return (
      <p className="text-sm text-zinc-500">
        No paper metrics found for this run.
      </p>
    );
  }
  return <MetricsEntries title="Paper metrics" entries={paper} />;
}

function MetricsEntries({
  title,
  entries,
}: {
  title: string;
  entries: Record<string, unknown>;
}) {
  const rows = Object.entries(entries ?? {});

  if (rows.length === 0) {
    return null;
  }

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase text-zinc-400">
        {title}
      </h3>
      <table className="w-full text-left text-xs">
        <tbody>
          {rows.map(([key, value]) => (
            <tr key={key} className="border-t border-zinc-800">
              <td className="py-1 pr-2 text-zinc-500">{key}</td>
              <td className="py-1 text-zinc-300">
                {typeof value === "object" && value !== null
                  ? JSON.stringify(value)
                  : String(value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
