// @vitest-environment jsdom
import { useLayoutEffect, useRef, useState } from "react";
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

describe("WR-C-08: RetryJobDialog idempotency key", () => {
  it("opens without throwing when crypto.randomUUID is unavailable and reuses one UUID-shaped key across attempts", async () => {
    const { fn, calls } = makeRetryFetch([
      { networkError: true },
      { status: 202, body: REFERENCE },
    ]);
    vi.stubGlobal("fetch", fn);
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.forEach((_, index) => {
          bytes[index] = (index * 31 + 7) % 256;
        });
        return bytes;
      },
    });

    render(
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
    expect(calls[0].headers["Idempotency-Key"]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(calls[0].headers["Idempotency-Key"]).toBe(
      calls[1].headers["Idempotency-Key"],
    );
  });
});

describe("WR-C-05: RetryJobDialog accessibility", () => {
  function Harness({ onChanged = vi.fn() }: { onChanged?: () => void }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          opener
        </button>
        <RetryJobDialog
          open={open}
          job={jobDetail()}
          onClose={() => setOpen(false)}
          onChanged={onChanged}
          onNavigate={vi.fn()}
        />
      </>
    );
  }

  it("focuses the Close button on open, restores focus to the opener on close", () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "opener" });
    opener.focus();
    fireEvent.click(opener);

    const close = screen.getByRole("button", { name: "Close" });
    expect(document.activeElement).toBe(close);

    fireEvent.click(close);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("traps Tab inside the dialog in both directions", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "opener" }));
    const close = screen.getByRole("button", { name: "Close" });
    const retry = screen.getByRole("button", { name: "Retry Job" });

    retry.focus();
    expect(fireEvent.keyDown(document, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(close);

    close.focus();
    expect(fireEvent.keyDown(document, { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(retry);
  });

  it("announces a failure via role=alert and describes the dialog by its body copy", async () => {
    const { fn } = makeRetryFetch([
      { status: 409, body: { detail: { code: "retry_exists" } } },
    ]);
    vi.stubGlobal("fetch", fn);
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "opener" }));

    const describedBy = screen.getByRole("dialog").getAttribute("aria-describedby") ?? "";
    expect(document.getElementById(describedBy)?.textContent).toContain(
      "This creates a new Job with the same type and payload",
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();

    expect(screen.getByRole("alert").textContent).toBe("This Job already has a retry.");
  });
});

type RetryFirstFrame = {
  alerts: number;
  bodyText: string;
  retryDisabled: boolean | null;
};

/**
 * Sibling probe: its useLayoutEffect runs in the same commit as the dialog,
 * before passive effects, so it sees the first committed frame of an opening.
 */
function RetryFrameProbe({
  open,
  frames,
}: {
  open: boolean;
  frames: RetryFirstFrame[];
}) {
  const wasOpen = useRef(false);
  useLayoutEffect(() => {
    if (open && !wasOpen.current) {
      const retry = Array.from(
        document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button'),
      ).find((b) => b.textContent === "Retry Job");
      frames.push({
        alerts: document.querySelectorAll('[role="alert"]').length,
        bodyText: document.body.textContent ?? "",
        retryDisabled: retry ? retry.disabled : null,
      });
    }
    wasOpen.current = open;
  });
  return null;
}

describe("UAT gap 3: clean first frame", () => {
  function GapHarness({ frames }: { frames: RetryFirstFrame[] }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          opener
        </button>
        <RetryJobDialog
          open={open}
          job={jobDetail()}
          onClose={() => setOpen(false)}
          onChanged={vi.fn()}
          onNavigate={vi.fn()}
        />
        <RetryFrameProbe open={open} frames={frames} />
      </>
    );
  }

  const open = () => fireEvent.click(screen.getByRole("button", { name: "opener" }));

  it("re-opens with no alert and no error text after a 409", async () => {
    const { fn } = makeRetryFetch([
      { status: 409, body: { detail: { code: "retry_exists" } } },
    ]);
    vi.stubGlobal("fetch", fn);
    const frames: RetryFirstFrame[] = [];
    render(<GapHarness frames={frames} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();
    const message = screen.getByRole("alert").textContent as string;
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    open();

    expect(frames).toHaveLength(2);
    expect(frames[1].alerts).toBe(0);
    expect(frames[1].bodyText).not.toContain(message);
    expect(frames[1].bodyText).not.toContain("Already submitted");
  });

  it("re-opens without the replay notice and with Retry Job enabled after a replayed 200", async () => {
    const { fn } = makeRetryFetch([
      { status: 200, body: REFERENCE, replayed: true },
    ]);
    vi.stubGlobal("fetch", fn);
    const frames: RetryFirstFrame[] = [];
    render(<GapHarness frames={frames} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await flush();
    expect(document.body.textContent).toContain("Already submitted — opening existing Job");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    open();

    expect(frames).toHaveLength(2);
    expect(frames[1].bodyText).not.toContain("Already submitted");
    expect(frames[1].alerts).toBe(0);
    expect(frames[1].retryDisabled).toBe(false);
  });

  it("focuses Close on the first opening and again after close + re-open", () => {
    render(<GapHarness frames={[]} />);
    open();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    open();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
  });
});
