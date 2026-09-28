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
 */
export function StrategyControlTrigger({
  strategyId,
  enabled,
}: StrategyControlTriggerProps) {
  const [open, setOpen] = useState(false);
  const capability = useMutationCapability();

  const actionLabel = enabled ? "Disable Strategy" : "Enable Strategy";
  const currentState = enabled ? "ENABLED" : "DISABLED";
  const targetState = enabled ? "DISABLED" : "ENABLED";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={capability.state !== "enabled"}
        className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {actionLabel}
      </button>
      {capability.state !== "enabled" && capability.reason ? (
        <span className="text-xs text-zinc-500">{capability.reason}</span>
      ) : null}

      <ControlConfirmDialog
        open={open}
        actionLabel={actionLabel}
        currentState={currentState}
        targetState={targetState}
        onConfirm={(reason) =>
          enabled
            ? disableStrategy(strategyId, reason)
            : enableStrategy(strategyId, reason)
        }
        onClose={() => setOpen(false)}
        onDone={() => dispatchControlChanged("strategy")}
      />
    </div>
  );
}
