// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    payload: {},
    retry_of_job_id: null,
    retried_as_job_id: null,
    retry_blocked: null,
    cancellation_mode: "step_boundary",
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
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
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
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
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
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    // The trigger renders disabled while the mutation-capability GET is in
    // flight; wait for it to resolve rather than asserting on first paint.
    const button = await screen.findByRole("button", { name: "Cancel Job…" });
    await waitFor(() => {
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });

    fireEvent.click(button);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("hides the Cancel Job… trigger for a terminal Job", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "failed", failure_reason: "handler_error" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByRole("button", { name: "Cancel Job…" })).toBeNull();
  });

  it("disables the Cancel Job… trigger with the D-21 reason when mutations are disabled", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_DISABLED });
    const job = jobDetail({ status: "queued" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    // Wait for the resolved "disabled" state (the reason text) — the trigger
    // is also disabled while the capability GET is still in flight, so
    // asserting `disabled` on first paint would pass vacuously.
    expect(
      await screen.findByText("Mutations disabled on this deployment"),
    ).toBeTruthy();
    const button = screen.getByRole("button", { name: "Cancel Job…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
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
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
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

describe("JobHeaderPanel — queued-only cancel gating (D-03a)", () => {
  it("disables Cancel with the inline reason for a running Job whose cancellation_mode is queued_only", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "running", cancellation_mode: "queued_only" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    expect(await screen.findByText("Not cancellable once running")).toBeTruthy();
    const button = screen.getByRole("button", { name: "Cancel Job…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps Cancel enabled for a queued Job whose cancellation_mode is queued_only", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "queued", cancellation_mode: "queued_only" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const button = await screen.findByRole("button", { name: "Cancel Job…" });
    await waitFor(() => {
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });
    expect(screen.queryByText("Not cancellable once running")).toBeNull();
  });

  it("keeps Cancel enabled for a running Job whose cancellation_mode is step_boundary", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "running", cancellation_mode: "step_boundary" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const button = await screen.findByRole("button", { name: "Cancel Job…" });
    await waitFor(() => {
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });
    expect(screen.queryByText("Not cancellable once running")).toBeNull();
  });
});

describe("JobHeaderPanel — Retry trigger (D-20)", () => {
  it("shows an enabled Retry trigger for a failed Job with no lineage/block, and opens RetryJobDialog", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "failed" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const button = await screen.findByRole("button", { name: "Retry" });
    await waitFor(() => {
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });

    fireEvent.click(button);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders no Retry trigger for succeeded, queued, or running Jobs", async () => {
    stubJobTypesFetch();
    for (const status of ["succeeded", "queued", "running"] as const) {
      const job = jobDetail({ status });
      render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      cleanup();
    }
  });

  it("shows only the capability reason when mutations are disabled, even with retried_as_job_id and retry_blocked also set", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_DISABLED });
    const job = jobDetail({
      status: "failed",
      retried_as_job_id: "33333333-4444-5555-6666-777788889999",
      retry_blocked: {
        code: "reconciliation_required",
        required_job_type: "reconciliation",
        strategy_id: "trend_following_daily",
      },
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    expect(
      await screen.findByText("Mutations disabled on this deployment"),
    ).toBeTruthy();
    expect(
      screen.queryByText("Already retried — see the linked retry Job below."),
    ).toBeNull();
    expect(
      screen.queryByText(
        "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry.",
      ),
    ).toBeNull();
    expect(screen.queryByRole("link", { name: "Run reconciliation" })).toBeNull();
    const button = screen.getByRole("button", { name: "Retry" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows only the already-retried reason when retried_as_job_id is set, even with retry_blocked also set", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "failed",
      retried_as_job_id: "33333333-4444-5555-6666-777788889999",
      retry_blocked: {
        code: "reconciliation_required",
        required_job_type: "reconciliation",
        strategy_id: "trend_following_daily",
      },
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    expect(
      await screen.findByText("Already retried — see the linked retry Job below."),
    ).toBeTruthy();
    expect(
      screen.queryByText(
        "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry.",
      ),
    ).toBeNull();
    expect(screen.queryByRole("link", { name: "Run reconciliation" })).toBeNull();
    const button = screen.getByRole("button", { name: "Retry" });
    await waitFor(() => {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it("shows the retry-blocked reason and a Run reconciliation link built from server-supplied fields", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "failed",
      retry_blocked: {
        code: "reconciliation_required",
        required_job_type: "reconciliation",
        strategy_id: "trend_following_daily",
      },
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    expect(
      await screen.findByText(
        "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry.",
      ),
    ).toBeTruthy();
    const link = screen.getByRole("link", { name: "Run reconciliation" });
    expect(link.getAttribute("href")).toBe(
      "/jobs/new?type=reconciliation&strategy_id=trend_following_daily",
    );
  });

  it("omits &strategy_id from the Run reconciliation link when retry_blocked.strategy_id is null", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "cancelled",
      retry_blocked: {
        code: "reconciliation_required",
        required_job_type: "reconciliation",
        strategy_id: null,
      },
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const link = await screen.findByRole("link", { name: "Run reconciliation" });
    expect(link.getAttribute("href")).toBe("/jobs/new?type=reconciliation");
  });
});

describe("JobHeaderPanel — retry lineage rows (D-20)", () => {
  it("renders Retry of Job / Retried as Job links only when the respective field is non-null", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "failed",
      retry_of_job_id: "aaaaaaaa-1111-2222-3333-444455556666",
      retried_as_job_id: "bbbbbbbb-1111-2222-3333-444455556666",
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    const retryOfLink = screen.getByRole("link", { name: "aaaaaaaa" });
    expect(retryOfLink.getAttribute("href")).toBe(
      "/jobs/aaaaaaaa-1111-2222-3333-444455556666",
    );
    const retriedAsLink = screen.getByRole("link", { name: "bbbbbbbb" });
    expect(retriedAsLink.getAttribute("href")).toBe(
      "/jobs/bbbbbbbb-1111-2222-3333-444455556666",
    );
  });

  it("renders no lineage rows when both fields are null", async () => {
    stubJobTypesFetch();
    const job = jobDetail({ status: "failed" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByText("Retry of Job")).toBeNull();
    expect(screen.queryByText("Retried as Job")).toBeNull();
  });
});

describe("JobHeaderPanel — failure_reason domain_conflict (OPS-08)", () => {
  it("renders through the generic Failure reason / Failure message rows, unchanged from Phase 19", async () => {
    stubJobTypesFetch();
    const job = jobDetail({
      status: "failed",
      failure_reason: "domain_conflict",
      failure_message: "strategy already has an open position",
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText("domain_conflict")).toBeTruthy();
    expect(
      screen.getByText("strategy already has an open position"),
    ).toBeTruthy();
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

// 20.1-14 (COMPAT-01): api_only gating and the Outcome next to the lifecycle status.
const CATALOG_WITH_API_ONLY: JobTypesCatalog = {
  mutations_enabled: true,
  items: [
    {
      job_type: "session_like",
      description: "d",
      cancellation_mode: "queued_only",
      console_submission: "api_only",
    },
    {
      job_type: "probe_type",
      description: "d",
      cancellation_mode: "step_boundary",
      console_submission: "interactive",
    },
  ],
};

describe("JobHeaderPanel — api_only and Outcome (20.1-14)", () => {
  it("no Retry control for an api_only job: the notice is shown instead", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_WITH_API_ONLY });
    const job = jobDetail({ job_type: "session_like", status: "failed" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    expect(await screen.findByText("Operated through the API in this version")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("an interactive failed job keeps its Retry control and shows no api_only notice", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_WITH_API_ONLY });
    const job = jobDetail({ job_type: "probe_type", status: "failed" });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const button = await screen.findByRole("button", { name: "Retry" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText("Operated through the API in this version")).toBeNull();
  });

  it("a paused operation job never renders as plain success", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_WITH_API_ONLY });
    const job = jobDetail({
      job_type: "session_like",
      status: "succeeded",
      outcome: "paused",
      outcome_reason: "working_order_commitments_unaccounted",
    });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const badge = await screen.findByText("Succeeded · Paused: working order");
    expect(badge.className).not.toContain("emerald");
  });

  it("api_only job with no outcome shows Outcome via API only and never a success badge", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_WITH_API_ONLY });
    const job = jobDetail({ job_type: "session_like", status: "succeeded", outcome: null });
    render(<JobHeaderPanel job={job} onChanged={vi.fn()} onNavigate={vi.fn()} />);

    const badge = await screen.findByText("Outcome via API only");
    expect(badge.className).not.toContain("emerald");
    expect(screen.queryByText("Succeeded")).toBeNull();
  });

  it("a succeeded interactive job without an outcome keeps plain success styling", async () => {
    stubJobTypesFetch({ status: 200, body: CATALOG_WITH_API_ONLY });
    render(
      <JobHeaderPanel
        job={jobDetail({ status: "succeeded" })}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const badge = await screen.findByText("Succeeded");
    expect(badge.className).toContain("emerald");
  });
});
