// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { RunHeaderPanel } from "./RunHeaderPanel";
import type { ArtifactCounts, RunDetailResponse, RunSummary } from "./RunHeaderPanel";
import type { ApiResult } from "@/lib/api";

afterEach(() => {
  cleanup();
});

const ARTIFACT_COUNTS: ArtifactCounts = {
  backtest_signals: 0,
  backtest_trades: 0,
  backtest_equity_snapshots: 0,
  risk_events: 0,
  paper_orders: 0,
  paper_fills: 0,
  execution_events: 0,
};

function runSummary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: "run-1",
    strategy_id: "trend_following_daily",
    display_name: "Trend Following Daily",
    run_type: "backtest",
    status: "succeeded",
    trigger_source: "manual",
    as_of_session: null,
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:05:00Z",
    parameters_snapshot: {},
    result_summary: null,
    error_message: null,
    job_id: null,
    ...overrides,
  };
}

function successResult(run: RunSummary): ApiResult<RunDetailResponse> {
  return {
    ok: true,
    data: { run, artifact_counts: ARTIFACT_COUNTS },
    endpoint: "/api/v1/runs/run-1",
    asOf: new Date("2026-01-01T00:10:00Z"),
  };
}

describe("RunHeaderPanel D-07 back-link", () => {
  it('renders "Created by" with a link "Job <job_id>" to /jobs/<job_id> when job_id is non-null', () => {
    render(
      <RunHeaderPanel
        loading={false}
        result={successResult(runSummary({ job_id: "abc-123" }))}
        refetch={vi.fn()}
      />,
    );

    expect(screen.getByText("Created by")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Job abc-123" });
    expect(link.getAttribute("href")).toBe("/jobs/abc-123");
  });

  it('renders no "Created by" row when job_id is null', () => {
    render(
      <RunHeaderPanel
        loading={false}
        result={successResult(runSummary({ job_id: null }))}
        refetch={vi.fn()}
      />,
    );

    expect(screen.queryByText("Created by")).toBeNull();
  });
});
