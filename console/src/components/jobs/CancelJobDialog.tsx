"use client";

import { useEffect, useState } from "react";
import { cancelJob } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/idempotencyKey";
import type { JobReference } from "./types";

type CancelJobDialogProps = {
  jobId: string;
  jobType: string;
  jobStatus: "queued" | "running";
  open: boolean;
  onClose: () => void;
  onCancelled: (reference: JobReference) => void;
};

const HEADING_ID = "cancel-job-dialog-heading";
const REASON_FIELD_ID = "cancel-job-dialog-reason";

/**
 * JOBUI-04/D-15: confirmation dialog for POST /api/v1/jobs/{id}/cancel.
 * A plain React-state overlay (accessible modal role/attributes set
 * below) -- the native HTML modal element is deliberately not used, per
 * the UI-SPEC implementation constraint (jsdom's implementation has no
 * showModal/close behavior). One Idempotency-Key per body mount (one
 * opening), reused across attempts, including a transport-failure retry
 * (T-19-10-03); a failed cancel keeps the dialog open and shows
 * mutationErrorMessage copy instead of closing. The exported component is a
 * shell; the body mounts fresh on every opening, so the first frame is clean
 * (UAT gap 3) and no effect resets state on open.
 */
export function CancelJobDialog({ open, ...bodyProps }: CancelJobDialogProps) {
  if (!open) {
    return null;
  }
  return <CancelJobDialogBody {...bodyProps} />;
}

function CancelJobDialogBody({
  jobId,
  jobType,
  jobStatus,
  onClose,
  onCancelled,
}: Omit<CancelJobDialogProps, "open">) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [idempotencyKey] = useState(() => newIdempotencyKey());

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  const shortId = jobId.slice(0, 8);
  const bodyCopy =
    jobStatus === "queued"
      ? "This Job has not started yet. Cancelling now stops it before it runs. This cannot be undone."
      : "This Job is running. Cancellation takes effect only at the next step boundary — it will not stop mid-step.";

  async function handleConfirm() {
    setSubmitting(true);
    setErrorMessage(null);
    const trimmed = reason.trim();
    const result = await cancelJob(
      jobId,
      trimmed === "" ? null : trimmed,
      idempotencyKey,
    );
    setSubmitting(false);
    if (result.ok) {
      onCancelled(result.data);
      onClose();
      return;
    }
    setErrorMessage(result.message);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/80">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={HEADING_ID}
        className="w-full max-w-md rounded border border-zinc-800 bg-zinc-900 p-6"
      >
        <h2 id={HEADING_ID} className="text-sm font-semibold text-zinc-100">
          {`Cancel Job ${jobType} · ${shortId}`}
        </h2>
        <p className="mt-3 text-sm text-zinc-300">{bodyCopy}</p>

        <div className="mt-4">
          <label
            htmlFor={REASON_FIELD_ID}
            className="text-xs text-zinc-400"
          >
            Reason (optional)
          </label>
          <textarea
            id={REASON_FIELD_ID}
            value={reason}
            maxLength={500}
            rows={3}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-sm text-zinc-200"
          />
          <p className="mt-1 text-xs text-zinc-500">Up to 500 characters</p>
        </div>

        {errorMessage ? (
          <p className="mt-3 text-xs text-red-400">{errorMessage}</p>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800"
          >
            Keep Job
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={submitting}
            className="rounded border border-red-700 bg-red-900 px-3 py-1 text-red-50 hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel Job
          </button>
        </div>
      </div>
    </div>
  );
}
