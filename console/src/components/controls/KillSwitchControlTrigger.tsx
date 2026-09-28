"use client";

import { useState } from "react";
import { tripKillSwitch, resetKillSwitch } from "@/lib/api";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ControlConfirmDialog } from "./ControlConfirmDialog";
import { dispatchControlChanged } from "./controlEvents";

type KillSwitchControlTriggerProps = {
  isTripped: boolean;
};

/**
 * CTRL-01: the neutral Trip/Reset Kill Switch trigger (matches the Cancel
 * Job… trigger's neutral styling — the destructive color signal stays on
 * ControlConfirmDialog's confirm button, never here). Mounted at all three
 * call sites (`/controls` Kill Switch section, inline `KillSwitchBanner`,
 * and — via KillSwitchPanel's `renderAction` slot — the System Status
 * screen is unaffected since it passes nothing). The caller supplies the
 * known current state (`isTripped`); this component never fetches it
 * itself (UI-SPEC honesty rule: no trigger renders when state is unknown —
 * that is the caller's responsibility to enforce by not mounting this).
 */
export function KillSwitchControlTrigger({
  isTripped,
}: KillSwitchControlTriggerProps) {
  const [open, setOpen] = useState(false);
  const capability = useMutationCapability();

  const actionLabel = isTripped ? "Reset Kill Switch" : "Trip Kill Switch";
  const currentState = isTripped ? "TRIPPED" : "ARMED";
  const targetState = isTripped ? "ARMED" : "TRIPPED";

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
        requiresTypedConfirmation={isTripped ? "RESET" : undefined}
        onConfirm={(reason) =>
          isTripped ? resetKillSwitch(reason) : tripKillSwitch(reason)
        }
        onClose={() => setOpen(false)}
        onDone={() => dispatchControlChanged("killswitch")}
      />
    </div>
  );
}
