// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { JobEventsPanel } from "./JobEventsPanel";
import type { JobEvent, JobEventsPage } from "../types";

function jobEvent(overrides: Partial<JobEvent> = {}): JobEvent {
  return {
    id: "evt-1",
    from_status: "queued",
    to_status: "running",
    event_type: "claimed",
    outcome: "accepted",
    event_at: "2026-01-01T00:00:00Z",
    requested_by: null,
    reason: null,
    requested_at: null,
    acknowledged_at: null,
    terminal_cause: null,
    details: null,
    ...overrides,
  };
}

function eventsPage(items: JobEvent[]): JobEventsPage {
  return { job_id: "job-1", count: items.length, items };
}

function makeEventsFetch(bodies: JobEventsPage[]) {
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

describe("JobEventsPanel", () => {
  it("renders event_type and the from -> to status transition", async () => {
    const { fn } = makeEventsFetch([eventsPage([jobEvent()])]);
    vi.stubGlobal("fetch", fn);

    render(<JobEventsPanel jobId="job-1" jobIsTerminal={false} />);
    await advance(0);

    expect(screen.getByText("claimed")).toBeTruthy();
    expect(screen.getByText("queued → running")).toBeTruthy();
  });

  it('renders "No events recorded yet." for an empty list', async () => {
    const { fn } = makeEventsFetch([eventsPage([])]);
    vi.stubGlobal("fetch", fn);

    render(<JobEventsPanel jobId="job-1" jobIsTerminal={false} />);
    await advance(0);

    expect(screen.getByText("No events recorded yet.")).toBeTruthy();
  });

  it("does not issue a second fetch after advancing 10000ms once jobIsTerminal is true", async () => {
    const { fn, calls } = makeEventsFetch([eventsPage([jobEvent()])]);
    vi.stubGlobal("fetch", fn);

    render(<JobEventsPanel jobId="job-1" jobIsTerminal={true} />);
    await advance(0);
    expect(calls.length).toBe(1);

    await advance(10000);
    expect(calls.length).toBe(1);
  });
});
