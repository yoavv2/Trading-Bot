"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { useApiQuery } from "@/lib/useApiQuery";
import { FetchMeta } from "@/components/FetchMeta";

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
 * (KILL-01). Refetches on every route change so the banner can never go stale while
 * an operator navigates the console. Three honest states — a safety indicator must
 * never fail silent:
 *   1. tripped -> full-width red banner
 *   2. fetch failed -> full-width amber "state unknown" banner
 *   3. armed -> renders nothing (status screen shows the armed state explicitly)
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

  if (!result.data.is_tripped) {
    return null;
  }

  const { last_changed_at, last_change_actor, last_change_reason } =
    result.data;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-red-700 bg-red-950 px-4 py-2 text-sm text-red-100">
      <span>
        <span className="font-bold">
          KILL SWITCH TRIPPED — order submission halted
        </span>
        {" — "}
        changed {last_changed_at} by {last_change_actor ?? "unknown"}
        {last_change_reason ? ` (${last_change_reason})` : ""}
      </span>
      <FetchMeta asOf={result.asOf} loading={loading} onRefresh={refetch} />
    </div>
  );
}
