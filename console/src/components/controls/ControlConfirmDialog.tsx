"use client";

import { useEffect, useRef, useState } from "react";
import { isOutcomeUncertain, type MutationResult } from "@/lib/api";
import { useDialogFocus } from "@/lib/useDialogFocus";

type ControlConfirmDialogProps = {
  open: boolean;
  actionLabel: string;
  currentState: string;
  targetState: string;
  requiresTypedConfirmation?: "RESET";
  onConfirm: (reason: string) => Promise<MutationResult<{ changed: boolean }>>;
  onClose: () => void;
  onDone?: () => void;
  /**
   * Fired on a failure that may still have been applied server-side
   * (transport failure, 5xx, unreadable 2xx) so the displays can re-verify
   * the true state instead of keeping the pre-mutation snapshot (WR-C-02).
   */
  onOutcomeUncertain?: () => void;
};

const HEADING_ID = "control-confirm-dialog-heading";
const BODY_ID = "control-confirm-dialog-body";
const REASON_FIELD_ID = "control-confirm-dialog-reason";
const REASON_HELP_ID = "control-confirm-dialog-reason-help";
const TYPED_FIELD_ID = "control-confirm-dialog-typed-confirmation";
const TYPED_HELP_ID = "control-confirm-dialog-typed-confirmation-help";
const MAX_REASON_LENGTH = 500;

/**
 * D-13/D-14: the single shared confirmation dialog reused verbatim across
 * all four control call sites (Trip/Reset Kill Switch, Enable/Disable
 * Strategy) — no other file under console/src renders a control
 * confirmation dialog. Follows CancelJobDialog's exact plain React-state
 * overlay/reset-on-open/Escape mechanics (role="dialog", aria-modal="true",
 * zinc-950/80 backdrop, zinc-900 panel, zinc-800 border) — the native HTML
 * modal element is deliberately not used, per the same jsdom-testability
 * constraint documented there. Sends no per-request replay-key header
 * (D-10): control mutations are idempotent by explicit target state, not
 * by key.
 *
 * State machine (D-14): a `changed: true` response closes the dialog
 * immediately; a `changed: false` response keeps it open, replaces the body
 * with an "Already {TARGET}" unchanged notice, hides the confirm
 * button, and switches the dismiss label to "Close"; an error keeps the
 * dialog open with the mapped message shown and the form still editable.
 * `onDone` fires after every successful (ok: true) response, whether or
 * not the state actually changed, but never on error. `onOutcomeUncertain`
 * fires only for failures that may have been applied anyway.
 */
export function ControlConfirmDialog({
  open,
  actionLabel,
  currentState,
  targetState,
  requiresTypedConfirmation,
  onConfirm,
  onClose,
  onDone,
  onOutcomeUncertain,
}: ControlConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const [typedValue, setTypedValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [unchanged, setUnchanged] = useState(false);

  const wasOpenRef = useRef(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const dismissRef = useRef<HTMLButtonElement>(null);
  // Bumped on every open/close transition so a response that lands after the
  // dialog was closed or re-opened can be recognized as belonging to a
  // previous opening (WR-C-01).
  const openingRef = useRef(0);

  useEffect(() => {
    if (open !== wasOpenRef.current) {
      openingRef.current += 1;
    }
    if (open && !wasOpenRef.current) {
      setReason("");
      setTypedValue("");
      setErrorMessage(null);
      setSubmitting(false);
      setUnchanged(false);
    }
    wasOpenRef.current = open;
  }, [open]);

  // WR-C-05: focus into the reason field on open, Tab trap, restore on close.
  useDialogFocus(open, panelRef, reasonRef);

  // The unchanged notice removes the reason field (and the confirm button the
  // operator just pressed); move focus to the Close button so it is not lost.
  useEffect(() => {
    if (open && unchanged) {
      dismissRef.current?.focus();
    }
  }, [open, unchanged]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handleKeyDown(event: KeyboardEvent) {
      // Not while a request is in flight: dismissing then would promise
      // "Keep Current State" while the PUT can still commit (WR-C-01).
      if (event.key === "Escape" && !submitting) {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose, submitting]);

  if (!open) {
    return null;
  }

  const trimmedReason = reason.trim();
  const reasonValid =
    trimmedReason.length > 0 && trimmedReason.length <= MAX_REASON_LENGTH;
  const typedValid = requiresTypedConfirmation
    ? typedValue.trim() === requiresTypedConfirmation
    : true;
  const confirmDisabled = submitting || !reasonValid || !typedValid;

  async function handleConfirm() {
    const opening = openingRef.current;
    setSubmitting(true);
    setErrorMessage(null);
    const result = await onConfirm(trimmedReason);
    if (!result.ok && isOutcomeUncertain(result)) {
      onOutcomeUncertain?.();
    }
    if (opening !== openingRef.current) {
      // The dialog was closed or re-opened while the request was in flight:
      // do not write this outcome into the new opening's state, but still let
      // the displays converge on a state change that did commit.
      if (result.ok) {
        onDone?.();
      }
      return;
    }
    setSubmitting(false);
    if (result.ok) {
      onDone?.();
      if (result.data.changed) {
        onClose();
      } else {
        setUnchanged(true);
      }
      return;
    }
    setErrorMessage(result.message);
  }

  const bodyCopy = unchanged
    ? `Already ${targetState} — no change (recorded)`
    : `Current state: ${currentState}. This will change it to: ${targetState}.`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/80">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={HEADING_ID}
        aria-describedby={BODY_ID}
        tabIndex={-1}
        className="w-full max-w-md rounded border border-zinc-800 bg-zinc-900 p-6 focus:outline-none"
      >
        <h2 id={HEADING_ID} className="text-sm font-semibold text-zinc-100">
          {actionLabel}
        </h2>
        <p
          // Re-keyed so the unchanged notice mounts as a fresh alert region
          // (a role added to an existing node is not reliably announced).
          key={unchanged ? "unchanged" : "prompt"}
          id={BODY_ID}
          role={unchanged ? "alert" : undefined}
          className="mt-3 text-sm text-zinc-300"
        >
          {bodyCopy}
        </p>

        {!unchanged ? (
          <>
            <div className="mt-4">
              <label htmlFor={REASON_FIELD_ID} className="text-xs text-zinc-400">
                Reason
              </label>
              <textarea
                id={REASON_FIELD_ID}
                ref={reasonRef}
                aria-describedby={REASON_HELP_ID}
                value={reason}
                rows={3}
                onChange={(event) => setReason(event.target.value)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-sm text-zinc-200"
              />
              <p id={REASON_HELP_ID} className="mt-1 text-xs text-zinc-500">
                Required, up to 500 characters
              </p>
            </div>

            {requiresTypedConfirmation ? (
              <div className="mt-4">
                <label htmlFor={TYPED_FIELD_ID} className="text-xs text-zinc-400">
                  Type RESET to confirm
                </label>
                <input
                  id={TYPED_FIELD_ID}
                  type="text"
                  aria-describedby={TYPED_HELP_ID}
                  value={typedValue}
                  onChange={(event) => setTypedValue(event.target.value)}
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-sm text-zinc-200"
                />
                <p id={TYPED_HELP_ID} className="mt-1 text-xs text-zinc-500">
                  Resetting the kill switch allows trading to resume.
                </p>
              </div>
            ) : null}
          </>
        ) : null}

        {errorMessage ? (
          <p role="alert" className="mt-3 text-xs text-red-400">
            {errorMessage}
          </p>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            ref={dismissRef}
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {unchanged ? "Close" : "Keep Current State"}
          </button>
          {!unchanged ? (
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={confirmDisabled}
              className="rounded border border-red-700 bg-red-900 px-3 py-1 text-red-50 hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {actionLabel}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
