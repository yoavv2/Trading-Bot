"use client";

import type { ApiResult } from "@/lib/api";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";

export type RunSummary = {
  run_id: string;
  strategy_id: string;
  display_name: string;
  run_type: string;
  status: string;
  trigger_source: string;
  as_of_session: string | null;
  started_at: string;
  completed_at: string | null;
  parameters_snapshot: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  error_message: string | null;
};

export type ArtifactCounts = {
  backtest_signals: number;
  backtest_trades: number;
  backtest_equity_snapshots: number;
  risk_events: number;
  paper_orders: number;
  paper_fills: number;
  execution_events: number;
};

export type RunDetailResponse = {
  run: RunSummary;
  artifact_counts: ArtifactCounts;
};

type RunHeaderPanelProps = {
  loading: boolean;
  result: ApiResult<RunDetailResponse> | null;
  refetch: () => void;
};

function statusColor(status: string): string {
  if (status === "succeeded") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  if (status === "running") return "text-amber-400";
  return "text-zinc-300";
}

const ARTIFACT_CHIP_ORDER: Array<{ key: keyof ArtifactCounts; label: string }> =
  [
    { key: "risk_events", label: "Risk events" },
    { key: "paper_orders", label: "Paper orders" },
    { key: "paper_fills", label: "Paper fills" },
    { key: "backtest_signals", label: "Backtest signals" },
    { key: "backtest_trades", label: "Backtest trades" },
    { key: "backtest_equity_snapshots", label: "Equity snapshots" },
    { key: "execution_events", label: "Execution events" },
  ];

/**
 * Run summary header (RUNS-03/RUNS-04 context): display name, status, run type,
 * trigger, session, timestamps, artifact counts, and the verbatim error message
 * when the run failed. The `/api/v1/runs/{runId}` fetch is owned by the run-detail
 * page (not here) so the resolved strategy_id/run_type can gate the sibling audit
 * panels below it without a second fetch of the same endpoint; this panel just
 * renders whatever query state it's handed, including its own FetchMeta/ErrorState.
 */
export function RunHeaderPanel({
  loading,
  result,
  refetch,
}: RunHeaderPanelProps) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Run</h2>
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
          <ErrorState failure={result} title="Failed to load run" />
        ) : (
          <RunHeaderContent data={result.data} />
        )}
      </div>
    </section>
  );
}

function RunHeaderContent({ data }: { data: RunDetailResponse }) {
  const { run, artifact_counts: artifactCounts } = data;

  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-semibold text-zinc-100">
          {run.display_name}
        </span>
        <span
          className={`text-xs font-bold uppercase ${statusColor(run.status)}`}
        >
          {run.status}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <dt className="text-zinc-500">Run type</dt>
        <dd className="text-zinc-300">{run.run_type}</dd>
        <dt className="text-zinc-500">Trigger</dt>
        <dd className="text-zinc-300">{run.trigger_source}</dd>
        <dt className="text-zinc-500">As-of session</dt>
        <dd className="text-zinc-300">{run.as_of_session ?? "—"}</dd>
        <dt className="text-zinc-500">Started</dt>
        <dd className="text-zinc-300">{run.started_at}</dd>
        <dt className="text-zinc-500">Completed</dt>
        <dd className="text-zinc-300">{run.completed_at ?? "—"}</dd>
        <dt className="text-zinc-500">Run ID</dt>
        <dd className="break-all text-zinc-300">{run.run_id}</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        {ARTIFACT_CHIP_ORDER.map(({ key, label }) => (
          <span
            key={key}
            className="rounded border border-zinc-700 bg-zinc-950 px-2 py-0.5 text-xs text-zinc-300"
          >
            {label}: {artifactCounts[key]}
          </span>
        ))}
      </div>
      {run.error_message ? (
        <pre className="whitespace-pre-wrap rounded bg-zinc-950 p-2 font-mono text-xs text-red-300">
          {run.error_message}
        </pre>
      ) : null}
    </div>
  );
}
