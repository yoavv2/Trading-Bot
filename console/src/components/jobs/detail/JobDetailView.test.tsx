// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { JobDetailView } from "./JobDetailView";
import { JobsTable } from "@/components/jobs/JobsTable";
import type {
  JobDetail,
  JobEventsPage,
  JobLogsPage,
  JobSummary,
  JobsResponse,
  JobTypesCatalog,
} from "../types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function jobSummary(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "sc6-job-1",
    job_type: "test_only_type",
    status: "succeeded",
    queued_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:01Z",
    completed_at: "2026-01-01T00:00:02Z",
    failure_reason: null,
    outcome_uncertain: false,
    cancellation_requested_at: null,
    progress: {
      percent: 100,
      step: null,
      current: null,
      total: null,
      progress_updated_at: null,
    },
    ...overrides,
  };
}

function jobsResponse(items: JobSummary[]): JobsResponse {
  return {
    filters: { status: null, job_type: null, limit: 50 },
    count: items.length,
    items,
  };
}

function jobDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: "sc6-job-1",
    job_type: "test_only_type",
    status: "running",
    queued_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:01Z",
    completed_at: null,
    failure_reason: null,
    outcome_uncertain: false,
    cancellation_requested_at: null,
    progress: {
      percent: null,
      step: null,
      current: null,
      total: null,
      progress_updated_at: null,
    },
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
    payload: {},
    retry_of_job_id: null,
    retried_as_job_id: null,
    retry_blocked: null,
    cancellation_mode: "step_boundary",
    ...overrides,
  };
}

function emptyLogsPage(jobId: string): JobLogsPage {
  return { job_id: jobId, items: [], count: 0, next_after_sequence: null, has_more: false };
}

function emptyEventsPage(jobId: string): JobEventsPage {
  return { job_id: jobId, count: 0, items: [] };
}

const CATALOG_ENABLED: JobTypesCatalog = { mutations_enabled: true, items: [] };

type RouterOptions = {
  jobTypes?: { status: number; body: unknown };
  jobsList?: JobsResponse[];
  details?: Record<string, JobDetail[]>;
  logs?: Record<string, JobLogsPage>;
  events?: Record<string, JobEventsPage>;
};

/**
 * Routes the single global fetch() by pathname, matching the detail
 * endpoint (/backend/api/v1/jobs/<id>) precisely so it is never confused
 * with its /logs, /events suffixes or the /jobs list endpoint. `details`
 * bodies are consumed in call order per jobId (repeating the last body
 * once exhausted) so a test can script a running -> terminal transition.
 */
function makeRouter(options: RouterOptions) {
  const detailCallCounts: Record<string, number> = {};
  const detailCalls: string[] = [];
  const jobsListCalls: string[] = [];
  let jobsListIdx = 0;

  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input.toString();
    const url = new URL(raw, "http://localhost");
    const path = url.pathname;

    if (path === "/backend/api/v1/job-types") {
      const route = options.jobTypes ?? { status: 200, body: CATALOG_ENABLED };
      return Promise.resolve(jsonResponse(route.status, route.body));
    }

    if (path === "/backend/api/v1/jobs") {
      jobsListCalls.push(raw);
      const list = options.jobsList ?? [jobsResponse([])];
      const body = list[Math.min(jobsListIdx, list.length - 1)];
      jobsListIdx += 1;
      return Promise.resolve(jsonResponse(200, body));
    }

    const detailMatch = path.match(/^\/backend\/api\/v1\/jobs\/([^/]+)$/);
    if (detailMatch) {
      const id = detailMatch[1];
      detailCalls.push(raw);
      detailCallCounts[id] = (detailCallCounts[id] ?? 0) + 1;
      const bodies = options.details?.[id];
      if (!bodies || bodies.length === 0) {
        throw new Error(`JobDetailView.test.tsx: no detail body scripted for job ${id}`);
      }
      const body = bodies[Math.min(detailCallCounts[id] - 1, bodies.length - 1)];
      return Promise.resolve(jsonResponse(200, body));
    }

    const logsMatch = path.match(/^\/backend\/api\/v1\/jobs\/([^/]+)\/logs$/);
    if (logsMatch) {
      const id = logsMatch[1];
      const page = options.logs?.[id] ?? emptyLogsPage(id);
      return Promise.resolve(jsonResponse(200, page));
    }

    const eventsMatch = path.match(/^\/backend\/api\/v1\/jobs\/([^/]+)\/events$/);
    if (eventsMatch) {
      const id = eventsMatch[1];
      const page = options.events?.[id] ?? emptyEventsPage(id);
      return Promise.resolve(jsonResponse(200, page));
    }

    throw new Error(`JobDetailView.test.tsx: unexpected fetch URL ${raw}`);
  });

  return { fn, detailCalls, jobsListCalls };
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("SC6: a test-only Job type flows through JobsTable and JobDetailView unchanged", () => {
  it("renders through the list as any other row and through detail with unmapped resources/result_summary rendered generically", async () => {
    const { fn } = makeRouter({
      jobsList: [jobsResponse([jobSummary()])],
      details: {
        "sc6-job-1": [
          jobDetail({
            status: "succeeded",
            resources: [
              { kind: "unknown_kind", id: "u-1", status: "done", links: { self: "/x" } },
            ],
            result_summary: { foo: { bar: 1 }, run_id: "r-9" },
          }),
        ],
      },
    });
    vi.stubGlobal("fetch", fn);

    const { unmount } = render(<JobsTable />);
    await advance(0);

    const table = screen.getByRole("table");
    expect(within(table).getByText("test_only_type")).toBeTruthy();
    expect(
      within(table).getByRole("link", { name: "View" }).getAttribute("href"),
    ).toBe("/jobs/sc6-job-1");
    unmount();
    cleanup();

    render(<JobDetailView jobId="sc6-job-1" />);
    await advance(0);

    expect(screen.getByText("unknown_kind: u-1")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "unknown_kind: u-1" })).toBeNull();

    expect(screen.getByText("foo")).toBeTruthy();
    expect(screen.getByText('{"bar":1}')).toBeTruthy();

    const runIdValue = screen.getByText("r-9");
    expect(runIdValue.tagName).not.toBe("A");
    expect(screen.queryByRole("link", { name: "r-9" })).toBeNull();
  });
});

describe("JobDetailView — resources[] linking (map 2)", () => {
  it("links a strategy_run resource to /runs/<id>", async () => {
    const { fn } = makeRouter({
      details: {
        "job-run": [
          jobDetail({
            id: "job-run",
            status: "succeeded",
            resources: [
              { kind: "strategy_run", id: "run-77", status: "succeeded", links: { self: "/x" } },
            ],
          }),
        ],
      },
    });
    vi.stubGlobal("fetch", fn);

    render(<JobDetailView jobId="job-run" />);
    await advance(0);

    const link = screen.getByRole("link", { name: "strategy_run: run-77" });
    expect(link.getAttribute("href")).toBe("/runs/run-77");
  });

  it("renders a linked resource while the Job is still running (D-05)", async () => {
    const { fn } = makeRouter({
      details: {
        "job-inflight": [
          jobDetail({
            id: "job-inflight",
            status: "running",
            resources: [
              { kind: "strategy_run", id: "run-88", status: "running", links: { self: "/x" } },
            ],
          }),
        ],
      },
    });
    vi.stubGlobal("fetch", fn);

    render(<JobDetailView jobId="job-inflight" />);
    await advance(0);

    const link = screen.getByRole("link", { name: "strategy_run: run-88" });
    expect(link.getAttribute("href")).toBe("/runs/run-88");
  });
});

describe("JobDetailView — polling (JOBUI-05)", () => {
  it("polls every 3s while non-terminal and stops the instant status becomes terminal", async () => {
    const { fn, detailCalls } = makeRouter({
      details: {
        "job-poll": [
          jobDetail({
            id: "job-poll",
            status: "running",
            progress: {
              percent: null,
              step: "running backtest",
              current: null,
              total: null,
              progress_updated_at: null,
            },
          }),
          jobDetail({ id: "job-poll", status: "succeeded" }),
        ],
      },
    });
    vi.stubGlobal("fetch", fn);

    render(<JobDetailView jobId="job-poll" />);
    await advance(0);

    expect(detailCalls.length).toBe(1);
    expect(screen.getByText("Auto-refreshing every 3s")).toBeTruthy();
    expect(screen.getByText("running backtest")).toBeTruthy();

    await advance(3000);
    expect(detailCalls.length).toBe(2);
    expect(
      screen.getByText("Auto-refresh stopped — Job finished"),
    ).toBeTruthy();

    await advance(15000);
    expect(detailCalls.length).toBe(2);
  });
});

describe("JobDetailView — empty-state copy (status/resources.length only)", () => {
  it("shows the non-terminal empty-resources copy for a running Job", async () => {
    const { fn } = makeRouter({
      details: {
        "job-empty-running": [
          jobDetail({ id: "job-empty-running", status: "running", resources: [] }),
        ],
      },
    });
    vi.stubGlobal("fetch", fn);

    render(<JobDetailView jobId="job-empty-running" />);
    await advance(0);

    expect(
      screen.getByText(
        "No linked resources yet. This panel updates automatically once the Job's run starts.",
      ),
    ).toBeTruthy();
  });

  it("shows the cancelled-before-start result_summary copy and D-14 label together", async () => {
    const { fn } = makeRouter({
      details: {
        "job-cancelled-empty": [
          jobDetail({
            id: "job-cancelled-empty",
            status: "cancelled",
            cancellation_cause: "operator_request",
            cancellation_requested_at: "2026-01-01T00:03:00Z",
            resources: [],
            result_summary: null,
          }),
        ],
      },
    });
    vi.stubGlobal("fetch", fn);

    render(<JobDetailView jobId="job-cancelled-empty" />);
    await advance(0);

    expect(
      screen.getByText(
        "No result summary — this Job was cancelled before it produced a result.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Cancelled before start — never executed")).toBeTruthy();
  });
});
