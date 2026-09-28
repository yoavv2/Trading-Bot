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
 *
 * A `changed: false` response means the caller's `isTripped` prop was
 * already stale when the dialog opened. `onDone` dispatches
 * `killswitch:changed`, which typically drives the caller to refetch and
 * flip `isTripped` while the dialog is still open showing the unchanged
 * notice. If the dialog's heading/body derived straight from the live
 * `isTripped` prop, that flip would rewrite the dialog underneath the
 * still-visible "Already {STATE}" notice (T-20-18-03 stale-state class,
 * caught in review, not in the original plan text). Instead, the dialog's
 * own label/state/onConfirm are frozen from `openedAsTripped`, a snapshot
 * taken only at the moment the trigger button is clicked; the trigger
 * button itself still reflects the always-live prop.
 */
export function KillSwitchControlTrigger({
  isTripped,
}: KillSwitchControlTriggerProps) {
  const [openedAsTripped, setOpenedAsTripped] = useState<boolean | null>(null);
  const capability = useMutationCapability();

  const triggerLabel = isTripped ? "Reset Kill Switch" : "Trip Kill Switch";
  const dialogIsTripped = openedAsTripped ?? isTripped;
  const actionLabel = dialogIsTripped ? "Reset Kill Switch" : "Trip Kill Switch";
  const currentState = dialogIsTripped ? "TRIPPED" : "ARMED";
  const targetState = dialogIsTripped ? "ARMED" : "TRIPPED";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => setOpenedAsTripped(isTripped)}
        disabled={capability.state !== "enabled"}
        className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {triggerLabel}
      </button>
      {capability.state !== "enabled" && capability.reason ? (
        <span className="text-xs text-zinc-500">{capability.reason}</span>
      ) : null}

      <ControlConfirmDialog
        open={openedAsTripped !== null}
        actionLabel={actionLabel}
        currentState={currentState}
        targetState={targetState}
        requiresTypedConfirmation={dialogIsTripped ? "RESET" : undefined}
        onConfirm={(reason) =>
          dialogIsTripped ? resetKillSwitch(reason) : tripKillSwitch(reason)
        }
        onClose={() => setOpenedAsTripped(null)}
        onDone={() => dispatchControlChanged("killswitch")}
      />
    </div>
  );
}
