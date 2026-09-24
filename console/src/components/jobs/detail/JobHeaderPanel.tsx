"use client";

import { useState } from "react";
import Link from "next/link";
import { CancelJobDialog } from "../CancelJobDialog";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { cancellationOutcomeLabel } from "@/lib/cancellationLabel";
import { jobStatusColor, JOB_STATUS_BADGE_CLASS } from "@/lib/jobStatus";
import type { JobDetail } from "../types";

type JobHeaderPanelProps = {
  job: JobDetail;
  onChanged: () => void;
};

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
export function JobHeaderPanel({ job, onChanged }: JobHeaderPanelProps) {
  const [cancelOpen, setCancelOpen] = useState(false);
  const capability = useMutationCapability();
  const outcomeLabel = cancellationOutcomeLabel(job);
  // Narrow explicitly to the two statuses CancelJobDialog accepts -- the
  // "cancel trigger visible only while non-terminal" rule from the status
  // badge's own closed 5-value enum.
  const cancellableStatus: "queued" | "running" | null =
    job.status === "queued" || job.status === "running" ? job.status : null;

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
              disabled={capability.state !== "enabled"}
              className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel Job…
            </button>
            {capability.state !== "enabled" && capability.reason ? (
              <span className="text-xs text-zinc-500">
                {capability.reason}
              </span>
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
    </section>
  );
}
