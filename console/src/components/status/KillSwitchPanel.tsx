"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { StatusPanel } from "./StatusPanel";

type KillSwitchData = {
  name: string;
  state: "armed" | "tripped";
  is_tripped: boolean;
  last_changed_at: string;
  last_change_actor: string | null;
  last_change_reason: string | null;
  last_change_run_id: string | null;
};

/**
 * STAT-03: current kill-switch state with audit fields, visible on the status
 * screen even when the global banner is hidden (armed state renders nothing there).
 */
export function KillSwitchPanel() {
  const { loading, result, refetch } = useApiQuery<KillSwitchData>(
    "/api/v1/system/kill-switch",
  );

  return (
    <StatusPanel
      title="Kill Switch"
      loading={loading}
      result={result}
      refetch={refetch}
    >
      {(data) => (
        <div className="space-y-2 text-sm">
          <p
            className={`text-2xl font-bold ${
              data.is_tripped ? "text-red-400" : "text-emerald-400"
            }`}
          >
            {data.state.toUpperCase()}
          </p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <dt className="text-zinc-500">Last changed</dt>
            <dd className="text-zinc-300">{data.last_changed_at}</dd>
            <dt className="text-zinc-500">Actor</dt>
            <dd className="text-zinc-300">{data.last_change_actor ?? "—"}</dd>
            <dt className="text-zinc-500">Reason</dt>
            <dd className="text-zinc-300">
              {data.last_change_reason ?? "—"}
            </dd>
            <dt className="text-zinc-500">Run ID</dt>
            <dd className="break-all text-zinc-300">
              {data.last_change_run_id ?? "—"}
            </dd>
          </dl>
        </div>
      )}
    </StatusPanel>
  );
}
