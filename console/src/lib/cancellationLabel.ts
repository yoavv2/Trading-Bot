// D-14: a single, honest cancellation/outcome label composed from generic
// Job fields only — this function never branches on the kind of operation
// a Job represents, only on its lifecycle/cancellation state and the
// status of whatever resource it produced (if any). This is what lets the
// same function describe a cancelled/timed-out outcome for any future
// operation type unchanged.

import type { JobDetail } from "../components/jobs/types";

export type CancellationLabelInput = Pick<
  JobDetail,
  | "status"
  | "cancellation_requested_at"
  | "cancellation_acknowledged_at"
  | "cancellation_cause"
  | "failure_reason"
  | "outcome_uncertain"
  | "resources"
>;

const IN_PROGRESS_RESOURCE_STATUSES = new Set(["pending", "queued", "running"]);

/**
 * Renders only when at least one of cancellation_requested_at,
 * cancellation_cause, or a cancellation_timeout failure_reason is present —
 * every other Job renders its normal status/failure display instead, not
 * this label. Returns null when no rule below matches.
 */
export function cancellationOutcomeLabel(
  job: CancellationLabelInput,
): string | null {
  const {
    status,
    cancellation_requested_at,
    cancellation_acknowledged_at,
    cancellation_cause,
    failure_reason,
    resources,
  } = job;

  const isCancellationRelated =
    cancellation_requested_at !== null ||
    cancellation_cause !== null ||
    failure_reason === "cancellation_timeout";
  if (!isCancellationRelated) {
    return null;
  }

  const resourceStatus = resources[0]?.status.toLowerCase() ?? null;
  const resourceStatusLabel = resourceStatus?.toUpperCase() ?? null;
  const resourceInProgress =
    resourceStatus !== null && IN_PROGRESS_RESOURCE_STATUSES.has(resourceStatus);

  if (status === "cancelled" && cancellation_cause === "dependency_failed") {
    return "Cancelled automatically — a dependency failed; this Job never ran";
  }
  if (status === "cancelled" && cancellation_cause === "dependency_cancelled") {
    return "Cancelled automatically — a dependency was cancelled; this Job never ran";
  }
  if (status === "cancelled") {
    if (resources.length === 0) {
      return "Cancelled before start — never executed";
    }
    if (resourceInProgress) {
      return "Cancelled — linked resource is still in progress";
    }
    return `Cancelled — linked run had already completed (${resourceStatusLabel})`;
  }

  if (status === "failed" && failure_reason === "cancellation_timeout") {
    if (resources.length === 0) {
      return "Cancellation timed out — outcome uncertain; no linked resource found yet";
    }
    if (resourceStatus === "running") {
      return "Cancellation timed out — outcome uncertain; run still RUNNING";
    }
    return `Cancellation timed out — outcome uncertain, but linked run shows ${resourceStatusLabel}`;
  }

  if (status === "failed" && cancellation_requested_at !== null) {
    return `Cancellation requested, but the Job failed before acknowledging it (${failure_reason})`;
  }

  if (
    status === "running" &&
    cancellation_requested_at !== null &&
    cancellation_acknowledged_at === null
  ) {
    return "Cancellation requested — waiting for the next step boundary";
  }

  if (status === "succeeded" && cancellation_requested_at !== null) {
    return "Cancellation requested too late — the Job had already finished (SUCCEEDED)";
  }

  return null;
}
