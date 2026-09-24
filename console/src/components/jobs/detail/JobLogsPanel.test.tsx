// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { JobLogsPanel } from "./JobLogsPanel";
import type { JobLogLine, JobLogsPage } from "../types";

function logLine(sequence: number, message = `line ${sequence}`): JobLogLine {
  return {
    sequence,
    logged_at: `2026-01-01T00:00:0${sequence}Z`,
    level: "info",
    event_code: null,
    message,
    handler_type: null,
    context: null,
  };
}

function logsPage(overrides: Partial<JobLogsPage> = {}): JobLogsPage {
  return {
    job_id: "job-1",
    items: [],
    count: 0,
    next_after_sequence: null,
    has_more: false,
    ...overrides,
  };
}

function makeLogsFetch(bodies: JobLogsPage[]) {
  const calls: string[] = [];
  let callCount = 0;
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    const body = bodies[Math.min(callCount, bodies.length - 1)];
    callCount += 1;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });
  return { fn, calls };
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

describe("JobLogsPanel", () => {
  it("polls after 3000ms using the returned cursor and appends lines in ascending order", async () => {
    const { fn, calls } = makeLogsFetch([
      logsPage({ items: [logLine(1), logLine(2)], count: 2, next_after_sequence: 2 }),
      logsPage({ items: [logLine(3)], count: 1, next_after_sequence: 3 }),
    ]);
    vi.stubGlobal("fetch", fn);

    render(<JobLogsPanel jobId="job-1" jobIsTerminal={false} />);
    await advance(0);

    expect(calls.length).toBe(1);
    expect(screen.getAllByTestId("log-line").length).toBe(2);

    await advance(3000);

    expect(calls.length).toBe(2);
    expect(calls[1]).toContain("after_sequence=2");
    const rows = screen.getAllByTestId("log-line");
    expect(rows.length).toBe(3);
    expect(screen.getByText("line 1")).toBeTruthy();
    expect(screen.getByText("line 2")).toBeTruthy();
    expect(screen.getByText("line 3")).toBeTruthy();
  });

  it("has_more true on the first page triggers an immediate second request without advancing timers", async () => {
    const { fn, calls } = makeLogsFetch([
      logsPage({ items: [logLine(1)], count: 1, next_after_sequence: 1, has_more: true }),
      logsPage({ items: [logLine(2)], count: 1, next_after_sequence: 2, has_more: false }),
    ]);
    vi.stubGlobal("fetch", fn);

    render(<JobLogsPanel jobId="job-1" jobIsTerminal={false} />);
    await advance(0);

    expect(calls.length).toBe(2);
    expect(calls[1]).toContain("after_sequence=1");
    expect(screen.getAllByTestId("log-line").length).toBe(2);
  });

  it("fetches exactly once and never polls when jobIsTerminal is true", async () => {
    const { fn, calls } = makeLogsFetch([
      logsPage({ items: [logLine(1)], count: 1, next_after_sequence: 1 }),
    ]);
    vi.stubGlobal("fetch", fn);

    render(<JobLogsPanel jobId="job-1" jobIsTerminal={true} />);
    await advance(0);
    expect(calls.length).toBe(1);

    await advance(10000);
    expect(calls.length).toBe(1);
  });

  it("shows the non-terminal empty-state copy when jobIsTerminal is false and no lines exist", async () => {
    const { fn } = makeLogsFetch([logsPage()]);
    vi.stubGlobal("fetch", fn);

    render(<JobLogsPanel jobId="job-1" jobIsTerminal={false} />);
    await advance(0);

    expect(
      screen.getByText(
        "No log lines yet — this Job hasn't started emitting logs. This panel updates automatically once it does.",
      ),
    ).toBeTruthy();
  });

  it("shows the terminal empty-state copy when jobIsTerminal is true and no lines exist", async () => {
    const { fn } = makeLogsFetch([logsPage()]);
    vi.stubGlobal("fetch", fn);

    render(<JobLogsPanel jobId="job-1" jobIsTerminal={true} />);
    await advance(0);

    expect(
      screen.getByText("No log lines were recorded for this Job."),
    ).toBeTruthy();
  });

  it("renders an untrusted log message as literal text, never as HTML", async () => {
    const payload = "<img src=x onerror=alert(1)>";
    const { fn } = makeLogsFetch([
      logsPage({
        items: [logLine(1, payload)],
        count: 1,
        next_after_sequence: 1,
      }),
    ]);
    vi.stubGlobal("fetch", fn);

    const { container } = render(
      <JobLogsPanel jobId="job-1" jobIsTerminal={true} />,
    );
    await advance(0);

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(payload)).toBeTruthy();
  });
});
