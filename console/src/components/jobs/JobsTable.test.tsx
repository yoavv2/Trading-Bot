// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { JobsTable } from "./JobsTable";
import type { JobsResponse, JobSummary, JobTypesCatalog } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function job(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-1",
    job_type: "probe_type",
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

const CATALOG_ENABLED: JobTypesCatalog = {
  mutations_enabled: true,
  items: [{ job_type: "probe_type", description: "d", cancellation_mode: "step_boundary" }],
};

type JobTypesRoute = { status: number; body: unknown };

/**
 * Routes the console's single global fetch() by URL: GET /api/v1/job-types
 * (queried by useMutationCapability) and GET /api/v1/jobs... (queried by
 * JobsTable itself) are both dispatched through this one stub, matching how
 * the two live useApiQuery calls actually share global fetch. jobsBodies is
 * a sequence consumed in order by successive Jobs fetches (mount + each
 * poll/filter-driven refetch); the last body repeats once exhausted.
 */
function makeFetchRouter(options: { jobTypes?: JobTypesRoute; jobsBodies: JobsResponse[] }) {
  const jobTypes: JobTypesRoute = options.jobTypes ?? {
    status: 200,
    body: CATALOG_ENABLED,
  };
  const jobsCalls: string[] = [];
  let jobsCallCount = 0;

  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/job-types")) {
      return Promise.resolve(jsonResponse(jobTypes.status, jobTypes.body));
    }
    if (url.includes("/backend/api/v1/jobs")) {
      jobsCalls.push(url);
      const body =
        options.jobsBodies[Math.min(jobsCallCount, options.jobsBodies.length - 1)];
      jobsCallCount += 1;
      return Promise.resolve(jsonResponse(200, body));
    }
    throw new Error(`JobsTable.test.tsx: unexpected fetch URL ${url}`);
  });

  return { fn, jobsCalls };
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

describe("JobsTable rows", () => {
  it("renders a row per item with the status badge color class and a View link", async () => {
    const { fn } = makeFetchRouter({
      jobsBodies: [jobsResponse([job({ id: "job-1", status: "running" })])],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    const table = screen.getByRole("table");
    const badge = within(table).getByText("running");
    expect(badge.className).toContain("uppercase");
    expect(badge.className).toContain("text-amber-400");

    const viewLink = within(table).getByRole("link", { name: "View" });
    expect(viewLink.getAttribute("href")).toBe("/jobs/job-1");
  });

  it("renders a Job with an unrecognized job_type like any other row, without crashing", async () => {
    const { fn } = makeFetchRouter({
      jobsBodies: [
        jobsResponse([job({ id: "job-2", job_type: "never_seen_type", status: "succeeded" })]),
      ],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    const table = screen.getByRole("table");
    expect(within(table).getByText("never_seen_type")).toBeTruthy();
    expect(
      within(table).getByRole("link", { name: "View" }).getAttribute("href"),
    ).toBe("/jobs/job-2");
  });

  it("renders the failure reason with the outcome-uncertain suffix, or an em dash otherwise", async () => {
    const { fn } = makeFetchRouter({
      jobsBodies: [
        jobsResponse([
          job({
            id: "job-3",
            status: "failed",
            failure_reason: "handler_error",
            outcome_uncertain: true,
          }),
        ]),
      ],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    expect(screen.getByText("handler_error · outcome uncertain")).toBeTruthy();
  });
});

describe("JobsTable filters", () => {
  it("job type filter options come from the catalog and a chosen filter issues a scoped fetch", async () => {
    const { fn, jobsCalls } = makeFetchRouter({
      jobsBodies: [
        jobsResponse([job({ status: "succeeded" })]),
        jobsResponse([]),
        jobsResponse([]),
      ],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    const jobTypeSelect = screen.getByLabelText("Job type") as HTMLSelectElement;
    expect(within(jobTypeSelect).getByText("probe_type")).toBeTruthy();

    const statusSelect = screen.getByLabelText("Status") as HTMLSelectElement;
    fireEvent.change(statusSelect, { target: { value: "failed" } });
    await advance(0);
    fireEvent.change(jobTypeSelect, { target: { value: "probe_type" } });
    await advance(0);

    const lastJobsCall = jobsCalls.at(-1) as string;
    expect(lastJobsCall).toContain("status=failed");
    expect(lastJobsCall).toContain("job_type=probe_type");
  });

  it("shows the zero-Jobs-total empty state, then the zero-matches empty state once filtered", async () => {
    const { fn } = makeFetchRouter({
      jobsBodies: [jobsResponse([]), jobsResponse([])],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    expect(screen.getByText("No Jobs yet")).toBeTruthy();
    expect(screen.getByText("Submit a new Job to get started.")).toBeTruthy();

    const statusSelect = screen.getByLabelText("Status") as HTMLSelectElement;
    fireEvent.change(statusSelect, { target: { value: "failed" } });
    await advance(0);

    expect(screen.getByText("No Jobs match these filters.")).toBeTruthy();
    expect(screen.getByText("Clear filters or submit a new Job.")).toBeTruthy();
  });
});

describe("JobsTable polling (JOBUI-05)", () => {
  it("polls every 5s while a row is non-terminal and stops once every visible row is terminal", async () => {
    const { fn, jobsCalls } = makeFetchRouter({
      jobsBodies: [
        jobsResponse([job({ id: "job-1", status: "running" })]),
        jobsResponse([job({ id: "job-1", status: "succeeded" })]),
      ],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    expect(jobsCalls.length).toBe(1);
    expect(screen.getByText("Auto-refreshing every 5s")).toBeTruthy();

    await advance(5000);
    expect(jobsCalls.length).toBe(2);
    expect(
      screen.getByText("Auto-refresh stopped — all visible Jobs finished"),
    ).toBeTruthy();

    await advance(20000);
    expect(jobsCalls.length).toBe(2);
  });
});

describe("JobsTable New Job control (D-17/D-21)", () => {
  it("disables New Job with the D-21 reason when mutations are disabled", async () => {
    const { fn } = makeFetchRouter({
      jobTypes: { status: 200, body: { mutations_enabled: false, items: [] } },
      jobsBodies: [jobsResponse([])],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    const button = screen.getByRole("button", { name: "New Job" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });

  it("disables New Job with the honest-unknown reason when the catalog fetch fails", async () => {
    const { fn } = makeFetchRouter({
      jobTypes: { status: 500, body: { detail: "boom" } },
      jobsBodies: [jobsResponse([])],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    const button = screen.getByRole("button", { name: "New Job" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Mutation availability unknown — GET /api/v1/job-types failed"),
    ).toBeTruthy();
  });

  it("renders New Job as a link to /jobs/new when mutations are enabled", async () => {
    const { fn } = makeFetchRouter({
      jobsBodies: [jobsResponse([])],
    });
    vi.stubGlobal("fetch", fn);

    render(<JobsTable />);
    await advance(0);

    const link = screen.getByRole("link", { name: "New Job" });
    expect(link.getAttribute("href")).toBe("/jobs/new");
  });
});

// 20.1-14 (COMPAT-01): the Outcome next to the lifecycle status.
const CATALOG_WITH_API_ONLY = {
  mutations_enabled: true,
  items: [
    { job_type: "probe_type", description: "d", cancellation_mode: "step_boundary" },
    {
      job_type: "session_like",
      description: "d",
      cancellation_mode: "queued_only",
      console_submission: "api_only",
    },
  ],
};

async function renderRows(items: JobSummary[]) {
  const { fn } = makeFetchRouter({
    jobTypes: { status: 200, body: CATALOG_WITH_API_ONLY },
    jobsBodies: [jobsResponse(items)],
  });
  vi.stubGlobal("fetch", fn);
  render(<JobsTable />);
  await advance(0);
}

describe("JobsTable Outcome (20.1-14)", () => {
  it("a paused operation job never renders as plain success", async () => {
    await renderRows([
      job({
        job_type: "session_like",
        status: "succeeded",
        outcome: "paused",
        outcome_reason: "working_order_commitments_unaccounted",
      }),
    ]);

    const badge = screen.getByText("Succeeded · Paused: working order");
    expect(badge.className).not.toContain("emerald");
  });

  it("partial and blocked outcomes get warning styling, complete keeps success styling", async () => {
    await renderRows([
      job({
        id: "j-partial",
        status: "succeeded",
        outcome: "partial",
        outcome_detail: { failed_count: 2 },
      }),
      job({
        id: "j-blocked",
        job_type: "session_like",
        status: "succeeded",
        outcome: "blocked",
        outcome_reason: "kill_switch_tripped",
      }),
      job({ id: "j-complete", status: "succeeded", outcome: "complete" }),
    ]);

    expect(screen.getByText("Succeeded · Partial (2 symbols failed)").className).not.toContain(
      "emerald",
    );
    expect(
      screen.getByText("Succeeded · Blocked: kill switch tripped").className,
    ).not.toContain("emerald");
    expect(screen.getByText("Succeeded · Complete").className).toContain("emerald");
  });

  it("api_only job with no outcome shows Outcome via API only and never a success badge", async () => {
    await renderRows([job({ job_type: "session_like", status: "succeeded" })]);

    const badge = screen.getByText("Outcome via API only");
    expect(badge.className).not.toContain("emerald");
    expect(screen.queryByText("Succeeded")).toBeNull();
  });

  it("a non-api_only legacy succeeded job keeps its plain success badge; other statuses are unchanged", async () => {
    await renderRows([
      job({ id: "j-legacy", status: "succeeded" }),
      job({ id: "j-failed", status: "failed" }),
    ]);

    expect(screen.getByText("Succeeded").className).toContain("emerald");
    expect(screen.getAllByText("failed").some((el) => el.className.includes("red"))).toBe(true);
  });
});
