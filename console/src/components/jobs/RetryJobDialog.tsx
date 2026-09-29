"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { retryJob } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/idempotencyKey";
import { useDialogFocus } from "@/lib/useDialogFocus";
import type { JobDetail } from "./types";

type RetryJobDialogProps = {
  open: boolean;
  job: JobDetail;
  onClose: () => void;
  onChanged: () => void;
  onNavigate: (href: string) => void;
};

const HEADING_ID = "retry-job-dialog-heading";
const BODY_ID = "retry-job-dialog-body";

/**
 * Renders a payload value generically: a plain string/number/boolean value
 * as-is, a non-null object or array JSON.stringify'd. Never job-type-aware.
 */
function renderPayloadValue(value: unknown): string {
  if (value !== null && typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

/**
 * D-20: confirmation dialog for POST /api/v1/jobs/{id}/retry. Follows
 * CancelJobDialog's overlay mechanics. One Idempotency-Key per body mount
 * (one opening), reused across attempts, including a transport-failure
 * retry (T-20-17-01); the body mounts fresh on every opening, so the first
 * frame is clean (UAT gap 3) and no effect resets state on open. On a fresh
 * (202) or replayed (200) success the dialog navigates away immediately; on
 * any error it stays open, shows the mapped copy, and triggers a Job-detail
 * refetch via onChanged() so retried_as_job_id/retry_blocked are fresh once
 * the operator dismisses.
 */
export function RetryJobDialog({ open, ...bodyProps }: RetryJobDialogProps) {
  if (!open) {
    return null;
  }
  return <RetryJobDialogBody {...bodyProps} />;
}

function RetryJobDialogBody({
  job,
  onClose,
  onChanged,
  onNavigate,
}: Omit<RetryJobDialogProps, "open">) {
  const [submitting, setSubmitting] = useState(false);
  const [replayed, setReplayed] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [idempotencyKey] = useState(() => newIdempotencyKey());

  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // WR-C-05: focus the safe Close button on every opening (not the confirm
  // action, so a stray Enter cannot create a Job), Tab trap, restore focus on
  // unmount.
  useDialogFocus(true, panelRef, closeRef);

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

  const shortId = job.id.slice(0, 8);
  const payloadEntries = Object.entries(job.payload);

  async function handleConfirm() {
    setSubmitting(true);
    setErrorMessage(null);
    const result = await retryJob(job.id, idempotencyKey);
    setSubmitting(false);
    if (result.ok) {
      if (result.replayed) {
        setReplayed(true);
      }
      onNavigate(`/jobs/${result.data.job_id}`);
      return;
    }
    setErrorMessage(result.message);
    onChanged();
  }

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
          {`Retry Job ${job.job_type} · ${shortId}`}
        </h2>
        <p id={BODY_ID} className="mt-3 text-sm text-zinc-300">
          This creates a new Job with the same type and payload, linked to
          this one.
        </p>

        {payloadEntries.length > 0 ? (
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            {payloadEntries.map(([key, value]) => (
              <Fragment key={key}>
                <dt className="text-zinc-500">{key}</dt>
                <dd className="break-all font-mono text-zinc-300">
                  {renderPayloadValue(value)}
                </dd>
              </Fragment>
            ))}
          </dl>
        ) : null}

        {replayed ? (
          <p className="mt-3 text-xs text-zinc-400">
            Already submitted — opening existing Job
          </p>
        ) : null}

        {errorMessage ? (
          <p role="alert" className="mt-3 text-xs text-red-400">
            {errorMessage}
          </p>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800"
          >
            Close
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={submitting}
            className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Retry Job
          </button>
        </div>
      </div>
    </div>
  );
}
