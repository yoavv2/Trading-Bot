"use client";

import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { StrategyStatusBadge } from "@/components/strategy/StrategyStatusBadge";
import { useLastKnownData } from "@/lib/useLastKnownData";
import { StrategyControlTrigger } from "./StrategyControlTrigger";
import { useStrategyControlState } from "./useStrategyControlState";

const STRATEGY_ID = "trend_following_daily";

/**
 * D-13/CTRL-02: the /controls Strategy section. Current state comes from the DB
 * control-state read (`useStrategyControlState`), never the static config flag.
 * Honesty-first: when that read fails the section shows the shared ErrorState
 * and NO Enable/Disable trigger, because no target state can be computed
 * without a known current state.
 *
 * The trigger is mounted at one stable JSX position and receives `enabled` as
 * a prop; it is not swapped between two mount points on the boolean it toggles
 * (20-18 caller constraint). It also stays mounted across a failed re-read
 * (WR-C-06) so an open confirm dialog survives; while the read is failing only
 * the trigger button and the badge are withheld (`stateKnown={false}`).
 */
export function StrategyControlSection() {
  const { loading, result, refetch } = useStrategyControlState(STRATEGY_ID);
  const lastKnown = useLastKnownData(result);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Strategy</h2>
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : (
          <>
            {result.ok ? null : <ErrorState failure={result} />}
            {lastKnown ? (
              <div className="flex items-center gap-4 text-sm">
                {result.ok ? (
                  <StrategyStatusBadge enabled={result.data.status === "enabled"} />
                ) : null}
                <StrategyControlTrigger
                  strategyId={STRATEGY_ID}
                  enabled={lastKnown.status === "enabled"}
                  stateKnown={result.ok}
                />
              </div>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
