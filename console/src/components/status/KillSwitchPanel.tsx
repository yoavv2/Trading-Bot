"use client";

import type { ReactNode } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { useLastKnownData } from "@/lib/useLastKnownData";
import { ErrorState } from "@/components/ErrorState";
import { useControlChanged } from "@/components/controls/controlEvents";
import { StatusPanel } from "./StatusPanel";

export type KillSwitchData = {
  name: string;
  state: "armed" | "tripped";
  is_tripped: boolean;
  last_changed_at: string;
  last_change_actor: string | null;
  last_change_reason: string | null;
  last_change_run_id: string | null;
};

type KillSwitchPanelProps = {
  /**
   * Optional slot rendered beside the state display, fed from this panel's own
   * single fetch (never a second query that could disagree with the displayed
   * state). The System Status screen passes nothing; /controls passes the
   * Trip/Reset trigger.
   */
  renderAction?: (
    data: KillSwitchData,
    meta: { stateKnown: boolean },
  ) => ReactNode;
};

/**
 * STAT-03: current kill-switch state with audit fields, visible on the status
 * screen regardless of the global banner. Exactly one fetch per mounted panel;
 * refetches on the same-tab `killswitch:changed` event so it stays in sync with
 * any control mutation made elsewhere on the page (Plan 23 also makes the banner
 * render the armed state).
 *
 * WR-C-06: when a refetch fails while a `renderAction` dialog is open, the
 * failure branch keeps the action mounted at the same tree position (same
 * outer element, same flex row, action at the same index) fed with the last
 * known data and `stateKnown: false`, so the open dialog and the operator's
 * typed input survive; the caller withholds only the trigger button.
 */
export function KillSwitchPanel({ renderAction }: KillSwitchPanelProps = {}) {
  const { loading, result, refetch } = useApiQuery<KillSwitchData>(
    "/api/v1/system/kill-switch",
  );
  useControlChanged("killswitch", refetch);
  const lastKnown = useLastKnownData(result);

  return (
    <StatusPanel
      title="Kill Switch"
      loading={loading}
      result={result}
      refetch={refetch}
      renderError={
        renderAction && lastKnown
          ? (failure) => (
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-4">
                  <ErrorState failure={failure} />
                  {renderAction(lastKnown, { stateKnown: false })}
                </div>
              </div>
            )
          : undefined
      }
    >
      {(data) => (
        <div className="space-y-2 text-sm">
          {renderAction ? (
            <div className="flex items-center gap-4">
              <p
                className={`text-2xl font-bold ${
                  data.is_tripped ? "text-red-400" : "text-emerald-400"
                }`}
              >
                {data.state.toUpperCase()}
              </p>
              {renderAction(data, { stateKnown: true })}
            </div>
          ) : (
            <p
              className={`text-2xl font-bold ${
                data.is_tripped ? "text-red-400" : "text-emerald-400"
              }`}
            >
              {data.state.toUpperCase()}
            </p>
          )}
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
