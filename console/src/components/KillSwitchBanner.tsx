"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { useApiQuery } from "@/lib/useApiQuery";
import { FetchMeta } from "@/components/FetchMeta";
import { KillSwitchControlTrigger } from "@/components/controls/KillSwitchControlTrigger";
import { useControlChanged } from "@/components/controls/controlEvents";

type KillSwitchState = {
  name: string;
  state: "armed" | "tripped";
  is_tripped: boolean;
  last_changed_at: string;
  last_change_actor: string | null;
  last_change_reason: string | null;
  last_change_run_id: string | null;
};

const KILL_SWITCH_ENDPOINT = "/api/v1/system/kill-switch";

/**
 * Global safety banner mounted once in the root layout so every screen inherits it
 * (KILL-01). Refetches on every route change and on the same-tab
 * `killswitch:changed` window event so the banner can never go stale. Three
 * honest states — a safety indicator must never fail silent:
 *   1. tripped -> full-width red banner + inline "Reset Kill Switch" trigger
 *   2. fetch failed -> full-width amber "state unknown" banner, NO trigger (no
 *      target state can be computed without a known current state)
 *   3. armed -> slim single-row ARMED bar + inline "Trip Kill Switch" trigger
 *
 * The armed and tripped states share ONE JSX tree (same element types at the
 * same positions); only classes/message text differ, and the trigger receives
 * `isTripped` as a prop. This keeps KillSwitchControlTrigger mounted across a
 * state flip so its open confirm dialog survives (20-18 caller constraint).
 */
export function KillSwitchBanner() {
  const { loading, result, refetch } = useApiQuery<KillSwitchState>(
    KILL_SWITCH_ENDPOINT,
  );
  const pathname = usePathname();
  const previousPathname = useRef(pathname);

  useEffect(() => {
    if (previousPathname.current !== pathname) {
      previousPathname.current = pathname;
      refetch();
    }
  }, [pathname, refetch]);

  useControlChanged("killswitch", refetch);

  if (!result) {
    return null;
  }

  if (!result.ok) {
    const statusLabel = result.status === null ? "unreachable" : result.status;
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-700 bg-amber-950/80 px-4 py-2 text-sm text-amber-100">
        <span className="font-semibold">
          Kill-switch state UNKNOWN — GET {KILL_SWITCH_ENDPOINT} failed (
          {statusLabel})
        </span>
        <FetchMeta asOf={result.asOf} loading={loading} onRefresh={refetch} />
      </div>
    );
  }

  const { is_tripped, last_changed_at, last_change_actor, last_change_reason } =
    result.data;

  return (
    <div
      className={
        is_tripped
          ? "flex flex-wrap items-center justify-between gap-2 border-b border-red-700 bg-red-950 px-4 py-2 text-sm text-red-100"
          : "flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-1 text-xs text-zinc-400"
      }
    >
      <span>
        {is_tripped ? (
          <>
            <span className="font-bold">
              KILL SWITCH TRIPPED — order submission halted
            </span>
            {" — "}
            changed {last_changed_at} by {last_change_actor ?? "unknown"}
            {last_change_reason ? ` (${last_change_reason})` : ""}
          </>
        ) : (
          "Kill switch: ARMED"
        )}
      </span>
      <div className="flex items-center gap-3">
        <FetchMeta asOf={result.asOf} loading={loading} onRefresh={refetch} />
        <KillSwitchControlTrigger isTripped={is_tripped} />
      </div>
    </div>
  );
}
