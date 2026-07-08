"use client";

import Link from "next/link";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";

// Mirrors LatestRunPanel's RunItem shape — the verified response of
// GET /api/v1/runs (build_collection_response + _serialize_run_summary).
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

type RunsResponse = {
  filters: Record<string, unknown>;
  count: number;
  items: RunItem[];
};

type RunsTableProps = {
  runType: string;
  status: string;
};

function statusColor(status: string): string {
  if (status === "succeeded") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  if (status === "running") return "text-amber-400";
  return "text-zinc-300";
}

function buildRunsEndpoint(runType: string, status: string): string {
  const params = new URLSearchParams({ limit: "100" });
  if (runType) params.set("run_type", runType);
  if (status) params.set("status", status);
  return `/api/v1/runs?${params.toString()}`;
}

/**
 * RUNS-01/RUNS-02: filterable table over GET /api/v1/runs. The endpoint string
 * is rebuilt from the runType/status props on every render, so a filter change
 * produces a new endpoint and useApiQuery re-fetches with the matching
 * run_type/status query params applied SERVER-SIDE — this is not a client-side
 * .filter() over a fixed result set.
 */
export function RunsTable({ runType, status }: RunsTableProps) {
  const runsEndpoint = buildRunsEndpoint(runType, status);
  const { loading, result, refetch } = useApiQuery<RunsResponse>(runsEndpoint);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Runs</h2>
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
          <ErrorState failure={result} />
        ) : result.data.count === 0 || result.data.items.length === 0 ? (
          <p className="text-sm text-zinc-500">No runs match these filters.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500">
                <th className="py-2 pr-4">Run type</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Session</th>
                {/*
                  HONESTY (RUNS-01 "created_at"): the runs serializer exposes
                  started_at / completed_at / as_of_session — it does NOT expose
                  a distinct created_at field, and adding one would be an
                  unauthorized backend change. This column satisfies the
                  RUNS-01 "created_at" requirement with started_at, labeled
                  "Started". Do not render a fabricated created_at.
                */}
                <th className="py-2 pr-4">Started</th>
                <th className="py-2 pr-4">Error</th>
                <th className="py-2 pr-4">Detail</th>
              </tr>
            </thead>
            <tbody>
              {result.data.items.map((run) => (
                <tr
                  key={run.run_id}
                  className="border-b border-zinc-900 text-zinc-300"
                >
                  <td className="py-2 pr-4">{run.run_type}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`text-xs font-bold uppercase ${statusColor(
                        run.status,
                      )}`}
                    >
                      {run.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4">{run.as_of_session ?? "—"}</td>
                  <td className="py-2 pr-4">{run.started_at ?? "—"}</td>
                  <td className="py-2 pr-4">
                    {run.error_message ? (
                      <span className="rounded bg-red-950/60 px-2 py-0.5 text-xs font-semibold text-red-300">
                        error
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 pr-4">
                    <Link
                      href={`/runs/${run.run_id}`}
                      className="text-xs font-semibold text-sky-400 hover:underline"
                    >
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
