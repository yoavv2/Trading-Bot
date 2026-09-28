// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RetryJobDialog } from "./RetryJobDialog";
import type { JobDetail, JobReference } from "./types";

function jobDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: "11112222-3333-4444-5555-666677778888",
    job_type: "probe_type",
    status: "failed",
    queued_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:01Z",
    completed_at: "2026-01-01T00:00:02Z",
    failure_reason: "handler_error",
    outcome_uncertain: false,
    cancellation_requested_at: null,
    progress: {
      percent: null,
      step: null,
      current: null,
      total: null,
      progress_updated_at: null,
    },
    failure_message: "boom",
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
    payload: { strategy_id: "x", window: { a: 1 } },
    retry_of_job_id: null,
    retried_as_job_id: null,
    retry_blocked: null,
    cancellation_mode: "step_boundary",
    ...overrides,
  };
}

const REFERENCE: JobReference = {
  job_id: "99998888-7777-6666-5555-444433332222",
  job_type: "probe_type",
  status: "queued",
  links: { self: "", progress: "", logs: "", events: "" },
};

type RetryFetchResponse =
  | { networkError: true }
  | { status: number; body: unknown; replayed?: boolean };

type CapturedCall = {
  url: string;
  headers: Record<string, string>;
};

function makeRetryFetch(responses: RetryFetchResponse[]) {
  const calls: CapturedCall[] = [];
  let callCount = 0;
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({ url, headers });

    const response = responses[Math.min(callCount, responses.length - 1)];
    callCount += 1;

    if ("networkError" in response) {
      return Promise.reject(new Error("network down"));
    }
    const responseHeaders: Record<string, string> = {
      "content-type": "application/json",
    };
    if (response.replayed) {
      responseHeaders["Idempotency-Replayed"] = "true";
    }
    return Promise.resolve(
      new Response(JSON.stringify(response.body), {
        status: response.status,
        headers: responseHeaders,
      }),
    );
  });
  return { fn, calls };
}

let uuidCounter = 0;

function stubRandomUUID() {
  uuidCounter = 0;
  vi.stubGlobal("crypto", {
    ...globalThis.crypto,
    randomUUID: () => {
      uuidCounter += 1;
      return `uuid-${uuidCounter}`;
    },
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  stubRandomUUID();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RetryJobDialog", () => {
  it("renders nothing when open is false, and role=dialog with aria-modal=true when open", () => {
    const { rerender } = render(
      <RetryJobDialog
        open={false}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();

    rerender(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("renders the heading, body copy, and payload rows generically", () => {
    render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByText("Retry Job probe_type · 11112222")).toBeTruthy();
    expect(
      screen.getByText(
        "This creates a new Job with the same type and payload, linked to this one.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("strategy_id")).toBeTruthy();
    expect(screen.getByText("x")).toBeTruthy();
    expect(screen.getByText("window")).toBeTruthy();
    expect(screen.getByText('{"a":1}')).toBeTruthy();
  });

  it("reuses the same Idempotency-Key across a network-failure retry, and issues a new key after reopening", async () => {
    const { fn, calls } = makeRetryFetch([
      { networkError: true },
      { status: 202, body: REFERENCE },
    ]);
    vi.stubGlobal("fetch", fn);

    const { rerender } = render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();

    expect(calls.length).toBe(2);
    expect(calls[0].headers["Idempotency-Key"]).toBe(
      calls[1].headers["Idempotency-Key"],
    );

    rerender(
      <RetryJobDialog
        open={false}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );
    rerender(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();

    expect(calls.length).toBe(3);
    expect(calls[2].headers["Idempotency-Key"]).not.toBe(
      calls[0].headers["Idempotency-Key"],
    );
  });

  it("202 fresh submission calls onNavigate to the new Job", async () => {
    const { fn } = makeRetryFetch([{ status: 202, body: REFERENCE }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();

    expect(onNavigate).toHaveBeenCalledWith(
      `/jobs/${REFERENCE.job_id}`,
    );
  });

  it("200 replayed shows the replay message and calls onNavigate", async () => {
    const { fn } = makeRetryFetch([
      { status: 200, body: REFERENCE, replayed: true },
    ]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();

    expect(
      screen.getByText("Already submitted — opening existing Job"),
    ).toBeTruthy();
    expect(onNavigate).toHaveBeenCalledWith(
      `/jobs/${REFERENCE.job_id}`,
    );
  });

  it("409 reconciliation_required shows the mapped message, keeps the dialog open, and calls onChanged once", async () => {
    const { fn } = makeRetryFetch([
      {
        status: 409,
        body: { detail: { code: "reconciliation_required" } },
      },
    ]);
    vi.stubGlobal("fetch", fn);
    const onChanged = vi.fn();
    const onClose = vi.fn();
    const onNavigate = vi.fn();

    render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={onClose}
        onChanged={onChanged}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();

    expect(
      screen.getByText(
        "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(onNavigate).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("Close dismisses the dialog", () => {
    const onClose = vi.fn();
    render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={onClose}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("Escape dismisses the dialog", () => {
    const onClose = vi.fn();
    render(
      <RetryJobDialog
        open={true}
        job={jobDetail()}
        onClose={onClose}
        onChanged={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
