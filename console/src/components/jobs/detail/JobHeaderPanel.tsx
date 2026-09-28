"use client";

import { useState } from "react";
import Link from "next/link";
import { CancelJobDialog } from "../CancelJobDialog";
import { RetryJobDialog } from "../RetryJobDialog";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { cancellationOutcomeLabel } from "@/lib/cancellationLabel";
import { jobStatusColor, JOB_STATUS_BADGE_CLASS } from "@/lib/jobStatus";
import type { JobDetail } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

type JobHeaderPanelProps = {
  job: JobDetail;
  onChanged: () => void;
  onNavigate: (href: string) => void;
};

// D-20 disabled-Retry reason precedence, evaluated in this exact order:
// (1) mutations not enabled, (2) a retry already exists, (3) the D-19
// reconcile-first server block. Only one reason is ever surfaced. Never
// keys off job_type -- gating is entirely a function of capability.state,
// job.retried_as_job_id, and job.retry_blocked.
type RetryGate =
  | { kind: "open" }
  | { kind: "capability"; reason: string | null }
  | { kind: "already-retried" }
  | { kind: "blocked"; requiredJobType: string; strategyId: string | null };

function computeRetryGate(
  job: JobDetail,
  capability: MutationCapability,
): RetryGate {
  if (capability.state !== "enabled") {
    return { kind: "capability", reason: capability.reason };
  }
  if (job.retried_as_job_id !== null) {
    return { kind: "already-retried" };
  }
  if (job.retry_blocked !== null) {
    return {
      kind: "blocked",
      requiredJobType: job.retry_blocked.required_job_type,
      strategyId: job.retry_blocked.strategy_id,
    };
  }
  return { kind: "open" };
}

/** Renders a cancellation-group `<dt>`/`<dd>` pair only when its value is
 * non-null -- these rows never appear for a Job with no cancellation
 * history (D-14 scope). */
function CancellationRow({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  if (value === null) {
    return null;
  }
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="text-zinc-300">{value}</dd>
    </>
  );
}

/**
 * JOBUI-02/JOBUI-04: generic Job detail header -- job_type + short id
 * heading, status badge, the D-14 honest cancellation/outcome label when
 * applicable, every generic field row (timestamps, failure reason/message,
 * outcome_uncertain, cancellation group, blocking/root-cause Job links,
 * dependencies), and the gated Cancel trigger (D-21). Zero job_type
 * conditionals -- every field renders generically off JobDetail.
 */
export function JobHeaderPanel({ job, onChanged, onNavigate }: JobHeaderPanelProps) {
  const [cancelOpen, setCancelOpen] = useState(false);
  const [retryOpen, setRetryOpen] = useState(false);
  const capability = useMutationCapability();
  const outcomeLabel = cancellationOutcomeLabel(job);
  // Narrow explicitly to the two statuses CancelJobDialog accepts -- the
  // "cancel trigger visible only while non-terminal" rule from the status
  // badge's own closed 5-value enum.
  const cancellableStatus: "queued" | "running" | null =
    job.status === "queued" || job.status === "running" ? job.status : null;

  // D-03a: a queued_only Job is not cancellable once it starts running --
  // this two-value boolean reads job.cancellation_mode, never job_type.
  const cancelBlockedWhileRunning =
    job.status === "running" && job.cancellation_mode === "queued_only";
  const cancelDisabled = cancelBlockedWhileRunning || capability.state !== "enabled";
  const cancelReason = cancelBlockedWhileRunning
    ? "Not cancellable once running"
    : capability.state !== "enabled"
      ? capability.reason
      : null;

  // D-20: the Retry trigger renders only for a terminal, non-succeeded Job.
  const showRetryTrigger = job.status === "failed" || job.status === "cancelled";
  const retryGate = computeRetryGate(job, capability);
  const retryDisabled = retryGate.kind !== "open";
  const retryReason =
    retryGate.kind === "capability"
      ? retryGate.reason
      : retryGate.kind === "already-retried"
        ? "Already retried — see the linked retry Job below."
        : retryGate.kind === "blocked"
          ? "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry."
          : null;
  const reconciliationHref =
    retryGate.kind === "blocked"
      ? `/jobs/new?type=${encodeURIComponent(retryGate.requiredJobType)}${
          retryGate.strategyId !== null
            ? `&strategy_id=${encodeURIComponent(retryGate.strategyId)}`
            : ""
        }`
      : null;

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold text-zinc-100">
            {`${job.job_type} · ${job.id.slice(0, 8)}`}
          </h1>
          <span
            className={`${JOB_STATUS_BADGE_CLASS} ${jobStatusColor(job.status)}`}
          >
            {job.status}
          </span>
        </div>

        {cancellableStatus ? (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setCancelOpen(true)}
              disabled={cancelDisabled}
              className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel Job…
            </button>
            {cancelReason ? (
              <span className="text-xs text-zinc-500">{cancelReason}</span>
            ) : null}
          </div>
        ) : null}

        {showRetryTrigger ? (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setRetryOpen(true)}
              disabled={retryDisabled}
              className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Retry
            </button>
            {retryReason ? (
              <span className="text-xs text-zinc-500">{retryReason}</span>
            ) : null}
            {reconciliationHref ? (
              <Link
                href={reconciliationHref}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                Run reconciliation
              </Link>
            ) : null}
          </div>
        ) : null}
      </div>

      {outcomeLabel ? (
        <p className="mt-2 text-sm text-zinc-300">{outcomeLabel}</p>
      ) : null}

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <dt className="text-zinc-500">Job ID</dt>
        <dd className="break-all font-mono text-zinc-300">{job.id}</dd>

        <dt className="text-zinc-500">Queued</dt>
        <dd className="text-zinc-300">{job.queued_at ?? "—"}</dd>

        <dt className="text-zinc-500">Started</dt>
        <dd className="text-zinc-300">{job.started_at ?? "—"}</dd>

        <dt className="text-zinc-500">Completed</dt>
        <dd className="text-zinc-300">{job.completed_at ?? "—"}</dd>

        <dt className="text-zinc-500">Failure reason</dt>
        <dd className="text-zinc-300">{job.failure_reason ?? "—"}</dd>

        <dt className="text-zinc-500">Failure message</dt>
        <dd className="text-zinc-300">{job.failure_message ?? "—"}</dd>

        <dt className="text-zinc-500">Outcome uncertain</dt>
        <dd className="text-zinc-300">{job.outcome_uncertain ? "Yes" : "No"}</dd>

        <CancellationRow
          label="Cancellation requested at"
          value={job.cancellation_requested_at}
        />
        <CancellationRow
          label="Requested by"
          value={job.cancellation_requested_by}
        />
        <CancellationRow label="Reason" value={job.cancellation_reason} />
        <CancellationRow
          label="Acknowledged at"
          value={job.cancellation_acknowledged_at}
        />
        <CancellationRow label="Cause" value={job.cancellation_cause} />

        {job.blocking_job_id ? (
          <>
            <dt className="text-zinc-500">Blocking Job</dt>
            <dd className="text-zinc-300">
              <Link
                href={`/jobs/${job.blocking_job_id}`}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                {`${job.blocking_job_id} (${job.blocking_job_status ?? "unknown"})`}
              </Link>
            </dd>
          </>
        ) : null}

        {job.root_cause_job_id ? (
          <>
            <dt className="text-zinc-500">Root cause Job</dt>
            <dd className="text-zinc-300">
              <Link
                href={`/jobs/${job.root_cause_job_id}`}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                {job.root_cause_job_id}
              </Link>
            </dd>
          </>
        ) : null}

        {job.retry_of_job_id ? (
          <>
            <dt className="text-zinc-500">Retry of Job</dt>
            <dd className="text-zinc-300">
              <Link
                href={`/jobs/${job.retry_of_job_id}`}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                {job.retry_of_job_id.slice(0, 8)}
              </Link>
            </dd>
          </>
        ) : null}

        {job.retried_as_job_id ? (
          <>
            <dt className="text-zinc-500">Retried as Job</dt>
            <dd className="text-zinc-300">
              <Link
                href={`/jobs/${job.retried_as_job_id}`}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                {job.retried_as_job_id.slice(0, 8)}
              </Link>
            </dd>
          </>
        ) : null}

        <dt className="text-zinc-500">Dependencies</dt>
        <dd className="text-zinc-300">
          {job.dependencies.length === 0 ? (
            "—"
          ) : (
            <div className="flex flex-col gap-1">
              {job.dependencies.map((dep) => (
                <Link
                  key={dep.id}
                  href={`/jobs/${dep.id}`}
                  className="text-xs font-semibold text-sky-400 hover:underline"
                >
                  {`${dep.job_type} · ${dep.status}`}
                </Link>
              ))}
            </div>
          )}
        </dd>
      </dl>

      {cancellableStatus ? (
        <CancelJobDialog
          jobId={job.id}
          jobType={job.job_type}
          jobStatus={cancellableStatus}
          open={cancelOpen}
          onClose={() => setCancelOpen(false)}
          onCancelled={() => {
            setCancelOpen(false);
            onChanged();
          }}
        />
      ) : null}

      {showRetryTrigger ? (
        <RetryJobDialog
          open={retryOpen}
          job={job}
          onClose={() => setRetryOpen(false)}
          onChanged={onChanged}
          onNavigate={onNavigate}
        />
      ) : null}
    </section>
  );
}
