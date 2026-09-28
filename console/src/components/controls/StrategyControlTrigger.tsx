"use client";

import { useState } from "react";
import { enableStrategy, disableStrategy } from "@/lib/api";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ControlConfirmDialog } from "./ControlConfirmDialog";
import { dispatchControlChanged } from "./controlEvents";

type StrategyControlTriggerProps = {
  strategyId: string;
  enabled: boolean;
};

/**
 * CTRL-02: the neutral Enable/Disable Strategy trigger, mounted at both
 * remaining call sites (`/controls` Strategy section, inline on
 * `/strategy`). Same shape as KillSwitchControlTrigger: the caller
 * supplies the known current `enabled` state (sourced from
 * `useStrategyControlState`, the DB control-status read, never the static
 * config flag) and this component never fetches it itself.
 *
 * Same stale-state guard as KillSwitchControlTrigger: a `changed: false`
 * response means `enabled` was already stale when the dialog opened, and
 * `onDone`'s `strategy:changed` dispatch typically flips it while the
 * dialog still shows the unchanged notice. The dialog's own label/state/
 * onConfirm are frozen from `openedAsEnabled`, a snapshot taken only when
 * the trigger button is clicked; the trigger button itself stays live.
 */
export function StrategyControlTrigger({
  strategyId,
  enabled,
}: StrategyControlTriggerProps) {
  const [openedAsEnabled, setOpenedAsEnabled] = useState<boolean | null>(null);
  const capability = useMutationCapability();

  const triggerLabel = enabled ? "Disable Strategy" : "Enable Strategy";
  const dialogIsEnabled = openedAsEnabled ?? enabled;
  const actionLabel = dialogIsEnabled ? "Disable Strategy" : "Enable Strategy";
  const currentState = dialogIsEnabled ? "ENABLED" : "DISABLED";
  const targetState = dialogIsEnabled ? "DISABLED" : "ENABLED";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => setOpenedAsEnabled(enabled)}
        disabled={capability.state !== "enabled"}
        className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {triggerLabel}
      </button>
      {capability.state !== "enabled" && capability.reason ? (
        <span className="text-xs text-zinc-500">{capability.reason}</span>
      ) : null}

      <ControlConfirmDialog
        open={openedAsEnabled !== null}
        actionLabel={actionLabel}
        currentState={currentState}
        targetState={targetState}
        onConfirm={(reason) =>
          dialogIsEnabled
            ? disableStrategy(strategyId, reason)
            : enableStrategy(strategyId, reason)
        }
        onClose={() => setOpenedAsEnabled(null)}
        onDone={() => dispatchControlChanged("strategy")}
        onOutcomeUncertain={() => dispatchControlChanged("strategy")}
      />
    </div>
  );
}
