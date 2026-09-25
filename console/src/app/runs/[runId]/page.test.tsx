// @vitest-environment jsdom
import { Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import RunDetailPage from "./page";

// Phase 19 UAT regression guard: opening a Job-created run (the D-07
// "Created by Job …" back-link case) must fetch the run once and render —
// no refetch/render/navigation loop. The loop seen during UAT was traced to
// a corrupted Turbopack dev cache, not this page; this pins the page-side
// half so a real code-level loop cannot hide behind that diagnosis.

const RUN_ID = "2b260e0d-db0e-4f5f-ba68-c04eb416ffaa";
const JOB_ID = "5b86f5f3-b2b0-4c41-852c-a0101513fc37";
const RUN_ENDPOINT = `/backend/api/v1/runs/${RUN_ID}`;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function runDetailBody() {
  return {
    run: {
      run_id: RUN_ID,
      strategy_id: "trend_following_daily",
      display_name: "TrendFollowingDailyV1",
      run_type: "backtest",
      status: "succeeded",
      trigger_source: "job",
      as_of_session: "2026-03-13",
      started_at: "2026-09-25T17:42:28Z",
      completed_at: "2026-09-25T17:42:41Z",
      parameters_snapshot: {},
      result_summary: null,
      error_message: null,
      job_id: JOB_ID,
    },
    artifact_counts: {
      backtest_signals: 0,
      backtest_trades: 0,
      backtest_equity_snapshots: 0,
      risk_events: 0,
      paper_orders: 0,
      paper_fills: 0,
      execution_events: 0,
    },
  };
}

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input.toString();
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("RunDetailPage — Job-created run (Phase 19 UAT regression)", () => {
  it("fetches the run once, renders the Created-by-Job back-link, and never refetches", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (urlOf(input) === RUN_ENDPOINT) {
        return Promise.resolve(jsonResponse(200, runDetailBody()));
      }
      // Sibling audit panels are out of scope here; an honest error state
      // keeps them inert without coupling this test to their payloads.
      return Promise.resolve(jsonResponse(503, { detail: "not under test" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    // Pre-settled thenable: React's use() reads status/value synchronously,
    // so the page renders without depending on scheduler timing under fake
    // timers.
    const params = Object.assign(Promise.resolve({ runId: RUN_ID }), {
      status: "fulfilled" as const,
      value: { runId: RUN_ID },
    });
    render(
      <Suspense fallback={null}>
        <RunDetailPage params={params} />
      </Suspense>,
    );
    await advance(0);
    await advance(0);

    const link = screen.getByRole("link", { name: `Job ${JOB_ID}` });
    expect(link.getAttribute("href")).toBe(`/jobs/${JOB_ID}`);

    const callsAfterRender = fetchMock.mock.calls.length;

    // Well past any polling interval used in the console: the run detail
    // page does not poll, so nothing may fire after the initial render.
    await advance(60_000);

    const urls = fetchMock.mock.calls.map(([input]) => urlOf(input));
    expect(urls.filter((u) => u === RUN_ENDPOINT)).toHaveLength(1);
    expect(fetchMock.mock.calls.length).toBe(callsAfterRender);
  });
});
