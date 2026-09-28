// Job domain types for the console Job UI (Phase 19, extended Phase 20).
// Verified against the actual backend response shapes:
//   - src/trading_platform/services/job_reads.py
//     (_serialize_job_summary, get_job_detail, list_job_logs, list_job_events;
//     Phase 20 composes retry lineage and the D-19 reconcile-first gate
//     into get_job_detail's response here)
//   - src/trading_platform/api/routes/jobs.py (list_jobs/job_detail/job_logs/job_events
//     job detail composition, including the Phase 20 retry route)
//   - src/trading_platform/api/routes/job_types.py (list_job_types)
//   - src/trading_platform/orchestration/job_mutations.py (JobReference.to_dict, _relative_links)
// String-literal unions use exactly the values in each closed backend enum.
// resources[].kind and resources[].status are typed as plain `string` (not
// closed unions) -- this is deliberate: it is what lets a test-only Job
// type with an unrecognized resources[].kind flow through the generic
// renderer unchanged (SC6/D-17), and StrategyRun.status carries its own
// closed vocabulary the console Job layer does not need to duplicate here.

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type JobFailureReason =
  | "handler_error"
  | "worker_lost"
  | "lease_expired"
  | "cancellation_timeout"
  | "config_invalid"
  | "domain_conflict";

export type JobCancellationMode = "step_boundary" | "queued_only";

export type RetryBlocked = {
  code: string;
  required_job_type: string;
  strategy_id: string | null;
};

export type JobCancellationCause =
  | "operator_request"
  | "dependency_failed"
  | "dependency_cancelled";

export type JobEventType =
  | "submitted"
  | "claimed"
  | "succeeded"
  | "failed"
  | "cancellation_requested"
  | "cancelled"
  | "lease_expired"
  | "worker_lost"
  | "cancellation_timeout"
  | "dependency_resolved";

export type JobEventOutcome = "accepted" | "rejected";

export type JobProgress = {
  percent: number | null;
  step: string | null;
  current: number | null;
  total: number | null;
  progress_updated_at: string | null;
};

export type JobSummary = {
  id: string;
  job_type: string;
  status: JobStatus;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  failure_reason: JobFailureReason | null;
  outcome_uncertain: boolean;
  cancellation_requested_at: string | null;
  progress: JobProgress;
};

export type JobsResponse = {
  filters: { status: string | null; job_type: string | null; limit: number };
  count: number;
  items: JobSummary[];
};

export type JobResource = {
  kind: string;
  id: string;
  status: string;
  links: { self?: string };
};

export type JobDependency = {
  id: string;
  job_type: string;
  status: JobStatus;
};

export type JobDetail = JobSummary & {
  failure_message: string | null;
  result_summary: Record<string, unknown> | null;
  cancellation_requested_by: string | null;
  cancellation_reason: string | null;
  cancellation_acknowledged_at: string | null;
  cancellation_cause: JobCancellationCause | null;
  blocking_job_id: string | null;
  blocking_job_status: JobStatus | null;
  root_cause_job_id: string | null;
  dependencies: JobDependency[];
  blocking_dependencies: JobDependency[];
  resources: JobResource[];
  payload: Record<string, unknown>;
  retry_of_job_id: string | null;
  retried_as_job_id: string | null;
  retry_blocked: RetryBlocked | null;
  cancellation_mode: JobCancellationMode | null;
};

export type JobLogLine = {
  sequence: number;
  logged_at: string;
  level: string;
  event_code: string | null;
  message: string;
  handler_type: string | null;
  context: Record<string, unknown> | null;
};

export type JobLogsPage = {
  job_id: string;
  items: JobLogLine[];
  count: number;
  next_after_sequence: number | null;
  has_more: boolean;
};

export type JobEvent = {
  id: string;
  from_status: JobStatus | null;
  to_status: JobStatus | null;
  event_type: JobEventType;
  outcome: JobEventOutcome;
  event_at: string;
  requested_by: string | null;
  reason: string | null;
  requested_at: string | null;
  acknowledged_at: string | null;
  terminal_cause: string | null;
  details: Record<string, unknown> | null;
};

export type JobEventsPage = {
  job_id: string;
  count: number;
  items: JobEvent[];
};

export type JobReference = {
  job_id: string;
  job_type: string;
  status: JobStatus;
  links: { self: string; progress: string; logs: string; events: string };
};

export type JobTypeCatalogItem = {
  job_type: string;
  description: string;
  cancellation_mode: JobCancellationMode;
  submission_defaults?: Record<string, string>;
};

export type JobTypesCatalog = {
  mutations_enabled: boolean;
  items: JobTypeCatalogItem[];
};
