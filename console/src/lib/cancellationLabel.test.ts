import { describe, expect, it } from "vitest";
import { cancellationOutcomeLabel } from "./cancellationLabel";
import { jobStatusColor, isTerminalJobStatus, TERMINAL_JOB_STATUSES } from "./jobStatus";
import type { JobDetail, JobResource } from "../components/jobs/types";

function resource(status: string): JobResource[] {
  return [{ kind: "strategy_run", id: "r1", status, links: { self: "/api/v1/runs/r1" } }];
}

function buildJob(overrides: Partial<JobDetail> & { job_type?: string }): JobDetail {
  return {
    id: "job-1",
    job_type: overrides.job_type ?? "backtest",
    status: "queued",
    queued_at: "2026-09-24T00:00:00Z",
    started_at: null,
    completed_at: null,
    failure_reason: null,
    outcome_uncertain: false,
    cancellation_requested_at: null,
    progress: { percent: null, step: null, current: null, total: null, progress_updated_at: null },
    failure_message: null,
    result_summary: null,
    cancellation_requested_by: null,
    cancellation_reason: null,
    cancellation_acknowledged_at: null,
    cancellation_cause: null,
    blocking_job_id: null,
    blocking_job_status: null,
    root_cause_job_id: null,
    dependencies: [],
    blocking_dependencies: [],
    resources: [],
    ...overrides,
  };
}

describe("cancellationOutcomeLabel (D-14)", () => {
  it("operator_request, no linked resource -> cancelled before start", () => {
    const job = buildJob({
      status: "cancelled",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      cancellation_cause: "operator_request",
      resources: [],
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancelled before start — never executed",
    );
  });

  it("operator_request, linked resource already succeeded -> completed (SUCCEEDED)", () => {
    const job = buildJob({
      status: "cancelled",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      cancellation_cause: "operator_request",
      resources: resource("succeeded"),
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancelled — linked run had already completed (SUCCEEDED)",
    );
  });

  it("operator_request, linked resource already failed -> completed (FAILED)", () => {
    const job = buildJob({
      status: "cancelled",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      cancellation_cause: "operator_request",
      resources: resource("failed"),
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancelled — linked run had already completed (FAILED)",
    );
  });

  it("operator_request, resource still running -> linked resource is still in progress", () => {
    const job = buildJob({
      status: "cancelled",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      cancellation_cause: "operator_request",
      resources: resource("running"),
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancelled — linked resource is still in progress",
    );
  });

  it("dependency_failed -> cancelled automatically, dependency failed", () => {
    const job = buildJob({
      status: "cancelled",
      cancellation_cause: "dependency_failed",
      resources: [],
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancelled automatically — a dependency failed; this Job never ran",
    );
  });

  it("dependency_cancelled -> cancelled automatically, dependency cancelled", () => {
    const job = buildJob({
      status: "cancelled",
      cancellation_cause: "dependency_cancelled",
      resources: [],
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancelled automatically — a dependency was cancelled; this Job never ran",
    );
  });

  it("cancellation_timeout, no linked resource -> outcome uncertain, no resource found yet", () => {
    const job = buildJob({
      status: "failed",
      failure_reason: "cancellation_timeout",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      resources: [],
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation timed out — outcome uncertain; no linked resource found yet",
    );
  });

  it("cancellation_timeout, resource still running -> outcome uncertain, run still RUNNING", () => {
    const job = buildJob({
      status: "failed",
      failure_reason: "cancellation_timeout",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      resources: resource("running"),
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation timed out — outcome uncertain; run still RUNNING",
    );
  });

  it("cancellation_timeout, resource shows succeeded -> outcome uncertain, linked run shows SUCCEEDED", () => {
    const job = buildJob({
      status: "failed",
      failure_reason: "cancellation_timeout",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      resources: resource("succeeded"),
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation timed out — outcome uncertain, but linked run shows SUCCEEDED",
    );
  });

  it("cancellation_timeout, resource shows failed -> outcome uncertain, linked run shows FAILED", () => {
    const job = buildJob({
      status: "failed",
      failure_reason: "cancellation_timeout",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      resources: resource("failed"),
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation timed out — outcome uncertain, but linked run shows FAILED",
    );
  });

  it("running, cancellation requested and not yet acknowledged -> waiting for the next step boundary", () => {
    const job = buildJob({
      status: "running",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      cancellation_acknowledged_at: null,
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation requested — waiting for the next step boundary",
    );
  });

  it("rule 7: failed with a cancellation request but a different failure_reason", () => {
    const job = buildJob({
      status: "failed",
      failure_reason: "handler_error",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation requested, but the Job failed before acknowledging it (handler_error)",
    );
  });

  it("rule 9 (defensive): succeeded with a cancellation request set", () => {
    const job = buildJob({
      status: "succeeded",
      cancellation_requested_at: "2026-09-24T00:00:00Z",
    });
    expect(cancellationOutcomeLabel(job)).toBe(
      "Cancellation requested too late — the Job had already finished (SUCCEEDED)",
    );
  });

  it("returns null when no cancellation field is set (plain failure)", () => {
    const job = buildJob({
      status: "failed",
      failure_reason: "config_invalid",
    });
    expect(cancellationOutcomeLabel(job)).toBeNull();
  });

  it("is job-type-agnostic: identical cancellation fields under two different job_type values yield the same label", () => {
    const base = {
      status: "cancelled" as const,
      cancellation_requested_at: "2026-09-24T00:00:00Z",
      cancellation_cause: "operator_request" as const,
      resources: [],
    };
    const jobA = buildJob({ ...base, job_type: "backtest" });
    const jobB = buildJob({ ...base, job_type: "some_future_operation" });
    expect(cancellationOutcomeLabel(jobA)).toBe(cancellationOutcomeLabel(jobB));
    expect(cancellationOutcomeLabel(jobA)).toBe(
      "Cancelled before start — never executed",
    );
  });
});

describe("jobStatusColor / isTerminalJobStatus", () => {
  it("maps each closed status to its UI-SPEC color class", () => {
    expect(jobStatusColor("queued")).toBe("text-zinc-400");
    expect(jobStatusColor("running")).toBe("text-amber-400");
    expect(jobStatusColor("succeeded")).toBe("text-emerald-400");
    expect(jobStatusColor("failed")).toBe("text-red-400");
    expect(jobStatusColor("cancelled")).toBe("text-zinc-500");
  });

  it("defaults an unrecognized status to text-zinc-300 rather than a blank badge", () => {
    expect(jobStatusColor("some_future_status")).toBe("text-zinc-300");
  });

  it("classifies terminal vs non-terminal statuses", () => {
    for (const status of TERMINAL_JOB_STATUSES) {
      expect(isTerminalJobStatus(status)).toBe(true);
    }
    expect(isTerminalJobStatus("queued")).toBe(false);
    expect(isTerminalJobStatus("running")).toBe(false);
  });
});
