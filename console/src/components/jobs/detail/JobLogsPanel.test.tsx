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
    expect(rows[0].textContent).toContain("line 1");
    expect(rows[1].textContent).toContain("line 2");
    expect(rows[2].textContent).toContain("line 3");
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

  it("continues draining past the per-tick page cap when the Job is terminal, instead of silently truncating the tail", async () => {
    const pages: JobLogsPage[] = [];
    for (let sequence = 1; sequence <= 25; sequence += 1) {
      pages.push(
        logsPage({
          items: [logLine(sequence)],
          count: 1,
          next_after_sequence: sequence,
          has_more: sequence < 25,
        }),
      );
    }
    const { fn, calls } = makeLogsFetch(pages);
    vi.stubGlobal("fetch", fn);

    render(<JobLogsPanel jobId="job-1" jobIsTerminal={true} />);
    // First tick drains 20 pages synchronously (bounded, T-19-10-04); the
    // page cap is hit with has_more still true, so a same-virtual-time
    // continuation (setTimeout 0) drains the remaining 5 -- both resolve
    // within a single advance(0) flush.
    await advance(0);

    expect(calls.length).toBe(25);
    expect(screen.getAllByTestId("log-line").length).toBe(25);

    await advance(10000);
    expect(calls.length).toBe(25);
  });

  it("discards a stale in-flight response after jobId changes, never appending it to the new Job's panel", async () => {
    let resolveFirst: (value: Response) => void = () => {};
    const firstPromise = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const calls: string[] = [];
    const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      if (calls.length === 1) {
        return firstPromise;
      }
      return Promise.resolve(
        new Response(
          JSON.stringify(
            logsPage({ items: [logLine(9)], count: 1, next_after_sequence: 9 }),
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    });
    vi.stubGlobal("fetch", fn);

    const { rerender } = render(
      <JobLogsPanel jobId="job-1" jobIsTerminal={true} />,
    );
    await advance(0); // job-1's fetch is in flight, deliberately unresolved

    rerender(<JobLogsPanel jobId="job-2" jobIsTerminal={true} />);
    await advance(0); // job-2's own fetch resolves

    expect(screen.getAllByTestId("log-line").length).toBe(1);
    expect(screen.getByText("line 9")).toBeTruthy();

    resolveFirst(
      new Response(
        JSON.stringify(
          logsPage({ items: [logLine(1)], count: 1, next_after_sequence: 1 }),
        ),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    await advance(0);

    expect(screen.queryByText("line 1")).toBeNull();
    expect(screen.getAllByTestId("log-line").length).toBe(1);
  });

  it("defers a terminal transition instead of overlapping an in-flight tick, then fetches once more and stops", async () => {
    let resolvePending: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      resolvePending = resolve;
    });
    const calls: string[] = [];
    const fn = vi.fn().mockImplementation(() => {
      calls.push("call");
      if (calls.length === 1) {
        return pending;
      }
      return Promise.resolve(
        new Response(JSON.stringify(logsPage()), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fn);

    const { rerender } = render(
      <JobLogsPanel jobId="job-1" jobIsTerminal={false} />,
    );
    await advance(0); // first tick in flight, not yet resolved

    rerender(<JobLogsPanel jobId="job-1" jobIsTerminal={true} />);
    await advance(0); // transition deferred -- no overlapping request yet

    expect(calls.length).toBe(1);

    resolvePending(
      new Response(JSON.stringify(logsPage()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await advance(0); // the deferred final fetch now runs

    expect(calls.length).toBe(2);

    await advance(10000);
    expect(calls.length).toBe(2);
  });
});
