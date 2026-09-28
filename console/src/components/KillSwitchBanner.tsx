"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { useApiQuery } from "@/lib/useApiQuery";
import { FetchMeta } from "@/components/FetchMeta";
import { KillSwitchControlTrigger } from "@/components/controls/KillSwitchControlTrigger";
import { useControlChanged } from "@/components/controls/controlEvents";
import { useLastKnownData } from "@/lib/useLastKnownData";

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
 * All three states share ONE JSX tree (same element types at the same
 * positions); only classes/message text differ, and the trigger receives
 * `isTripped` (the last known value) and `stateKnown` as props. This keeps
 * KillSwitchControlTrigger mounted across a state flip AND across a failed
 * re-read, so its open confirm dialog survives (20-18 caller constraint,
 * WR-C-06); while the state is unknown only the trigger button is withheld.
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
  const lastKnown = useLastKnownData(result);

  if (!result) {
    return null;
  }

  const stateKnown = result.ok;
  const isTripped = result.ok ? result.data.is_tripped : false;
  const statusLabel =
    !result.ok && result.status === null ? "unreachable" : !result.ok ? result.status : "";

  return (
    <div
      className={
        !result.ok
          ? "flex flex-wrap items-center justify-between gap-2 border-b border-amber-700 bg-amber-950/80 px-4 py-2 text-sm text-amber-100"
          : isTripped
            ? "flex flex-wrap items-center justify-between gap-2 border-b border-red-700 bg-red-950 px-4 py-2 text-sm text-red-100"
            : "flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-4 py-1 text-xs text-zinc-400"
      }
    >
      <span>
        {!result.ok ? (
          <span className="font-semibold">
            Kill-switch state UNKNOWN — GET {KILL_SWITCH_ENDPOINT} failed (
            {statusLabel})
          </span>
        ) : isTripped ? (
          <>
            <span className="font-bold">
              KILL SWITCH TRIPPED — order submission halted
            </span>
            {" — "}
            changed {result.data.last_changed_at} by{" "}
            {result.data.last_change_actor ?? "unknown"}
            {result.data.last_change_reason
              ? ` (${result.data.last_change_reason})`
              : ""}
          </>
        ) : (
          "Kill switch: ARMED"
        )}
      </span>
      <div className="flex items-center gap-3">
        <FetchMeta asOf={result.asOf} loading={loading} onRefresh={refetch} />
        {lastKnown ? (
          <KillSwitchControlTrigger
            isTripped={lastKnown.is_tripped}
            stateKnown={stateKnown}
          />
        ) : null}
      </div>
    </div>
  );
}
