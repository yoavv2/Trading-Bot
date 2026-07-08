"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { StatusPanel } from "./StatusPanel";

type RunItem = {
  run_id: string;
  strategy_id: string;
  display_name: string;
  run_type: string;
  status: string;
  trigger_source: string;
  as_of_session: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
};

type RunsData = {
  filters: Record<string, unknown>;
  count: number;
  items: RunItem[];
};

function statusColor(status: string): string {
  if (status === "succeeded") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  if (status === "running") return "text-amber-400";
  return "text-zinc-300";
}

/**
 * STAT-02: latest run of any type. GET /api/v1/runs?limit=1 defaults to
 * strategy_id=trend_following_daily with no run_type/status filter, ordered
 * started_at DESC, so items[0] is the latest run of any type.
 */
export function LatestRunPanel() {
  const { loading, result, refetch } = useApiQuery<RunsData>(
    "/api/v1/runs?limit=1",
  );

  return (
    <StatusPanel
      title="Latest Run"
      loading={loading}
      result={result}
      refetch={refetch}
    >
      {(data) => {
        if (data.count === 0 || data.items.length === 0) {
          return (
            <p className="text-sm text-zinc-500">No runs recorded yet</p>
          );
        }

        const run = data.items[0];

        return (
          <div className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-zinc-100">
                {run.display_name}
              </span>
              <span
                className={`text-xs font-bold uppercase ${statusColor(
                  run.status,
                )}`}
              >
                {run.status}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <dt className="text-zinc-500">Run type</dt>
              <dd className="text-zinc-300">{run.run_type}</dd>
              <dt className="text-zinc-500">Trigger</dt>
              <dd className="text-zinc-300">{run.trigger_source}</dd>
              <dt className="text-zinc-500">As-of session</dt>
              <dd className="text-zinc-300">{run.as_of_session ?? "—"}</dd>
              <dt className="text-zinc-500">Started</dt>
              <dd className="text-zinc-300">{run.started_at ?? "—"}</dd>
              <dt className="text-zinc-500">Completed</dt>
              <dd className="text-zinc-300">{run.completed_at ?? "—"}</dd>
            </dl>
            {run.error_message ? (
              <pre className="whitespace-pre-wrap rounded bg-zinc-950 p-2 font-mono text-xs text-red-300">
                {run.error_message}
              </pre>
            ) : null}
          </div>
        );
      }}
    </StatusPanel>
  );
}
