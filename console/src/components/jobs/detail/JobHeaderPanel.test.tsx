// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { JobHeaderPanel } from "./JobHeaderPanel";
import { JobProgressPanel } from "./JobProgressPanel";
import type { JobDetail, JobTypesCatalog } from "../types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function jobDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: "11111111-2222-3333-4444-555555555555",
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

const CATALOG_ENABLED: JobTypesCatalog = { mutations_enabled: true, items: [] };
const CATALOG_DISABLED: JobTypesCatalog = { mutations_enabled: false, items: [] };

function stubJobTypesFetch(catalog: { status: number; body: unknown } = { status: 200, body: CATALOG_ENABLED }) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/job-types")) {
      return Promise.resolve(jsonResponse(catalog.status, catalog.body));
    }
    throw new Error(`JobHeaderPanel.test.tsx: unexpected fetch URL ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("JobHeaderPanel — D-14 outcome label", () => {
  it("shows the honest outcome label for a cancelled Job whose linked run already succeeded", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "cancelled",
      cancellation_cause: "operator_request",
      cancellation_requested_at: "2026-01-01T00:05:00Z",
      resources: [
        {
          kind: "strategy_run",
          id: "run-1",
          status: "succeeded",
          links: { self: "/x" },
        },
      ],
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(
      screen.getByText(
        "Cancelled — linked run had already completed (SUCCEEDED)",
      ),
    ).toBeTruthy();
  });

  it("shows no cancellation-related label or Cancel control for a succeeded Job with no cancellation history", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "succeeded", completed_at: "2026-01-01T00:10:00Z" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryAllByText(/^Cancel/)).toHaveLength(0);
  });
});

describe("JobHeaderPanel — cancel trigger (D-21)", () => {
  it("shows an enabled Cancel Job… trigger for a running Job when mutations are enabled, and opens the confirmation dialog", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "running" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} />);

    const button = await screen.findByRole("button", { name: "Cancel Job…" });
    expect((button as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(button);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("hides the Cancel Job… trigger for a terminal Job", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "failed", failure_reason: "handler_error" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByRole("button", { name: "Cancel Job…" })).toBeNull();
  });

  it("disables the Cancel Job… trigger with the D-21 reason when mutations are disabled", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_DISABLED });
    const job = jobDetail({ status: "queued" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} />);

    const button = await screen.findByRole("button", { name: "Cancel Job…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Mutations disabled on this deployment"),
    ).toBeTruthy();
  });
});

describe("JobHeaderPanel — blocking Job link", () => {
  it("renders a link to /jobs/<blocking_job_id>", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "queued",
      blocking_job_id: "22222222-3333-4444-5555-666666666666",
      blocking_job_status: "running",
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    const link = screen.getByRole("link", {
      name: "22222222-3333-4444-5555-666666666666 (running)",
    });
    expect(link.getAttribute("href")).toBe(
      "/jobs/22222222-3333-4444-5555-666666666666",
    );
  });
});

describe("JobProgressPanel — D-16", () => {
  it("renders an indeterminate progressbar (no aria-valuenow) and the step text when percent is null", () => {
    render(
      <JobProgressPanel
        progress={{
          percent: null,
          step: "running backtest",
          current: null,
          total: null,
          progress_updated_at: null,
        }}
        status="running"
      />,
    );

    const bar = screen.getByRole("progressbar");
    expect(bar.hasAttribute("aria-valuenow")).toBe(false);
    expect(screen.getByText("running backtest")).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("renders aria-valuenow equal to a numeric percent", () => {
    render(
      <JobProgressPanel
        progress={{
          percent: 100,
          step: "done",
          current: 10,
          total: 10,
          progress_updated_at: null,
        }}
        status="succeeded"
      />,
    );

    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.getByText("10 / 10")).toBeTruthy();
  });
});
