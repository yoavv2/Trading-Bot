// @vitest-environment jsdom
import { useLayoutEffect, useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CancelJobDialog } from "./CancelJobDialog";
import type { JobReference } from "./types";

const REFERENCE: JobReference = {
  job_id: "12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  job_type: "probe_type",
  status: "cancelled",
  links: { self: "", progress: "", logs: "", events: "" },
};

type CancelFetchResponse =
  | { networkError: true }
  | { status: number; body: unknown };

type CapturedCall = {
  url: string;
  headers: Record<string, string>;
  body: unknown;
};

function makeCancelFetch(responses: CancelFetchResponse[]) {
  const calls: CapturedCall[] = [];
  let callCount = 0;
  const fn = vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const body = init?.body ? JSON.parse(init.body as string) : null;
      calls.push({ url, headers, body });

      const response = responses[Math.min(callCount, responses.length - 1)];
      callCount += 1;

      if ("networkError" in response) {
        return Promise.reject(new Error("network down"));
      }
      return Promise.resolve(
        new Response(JSON.stringify(response.body), {
          status: response.status,
          headers: { "content-type": "application/json" },
        }),
      );
    },
  );
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

describe("CancelJobDialog", () => {
  it("renders nothing when open is false, and role=dialog with aria-modal=true when open", () => {
    const { rerender } = render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={false}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();

    rerender(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("shows the QUEUED body and heading for a queued Job", () => {
    render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    expect(screen.getByText("Cancel Job probe_type · 12345678")).toBeTruthy();
    expect(
      screen.getByText(
        "This Job has not started yet. Cancelling now stops it before it runs. This cannot be undone.",
      ),
    ).toBeTruthy();
  });

  it("shows the RUNNING body text mentioning the next step boundary", () => {
    render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="running"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/next step boundary/),
    ).toBeTruthy();
  });

  it("sends reason: null for a whitespace-only reason, and the trimmed reason otherwise", async () => {
    const { fn, calls } = makeCancelFetch([
      { status: 200, body: REFERENCE },
    ]);
    vi.stubGlobal("fetch", fn);

    render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(calls[0].body).toEqual({ reason: null });
  });

  it("sends the trimmed reason when non-blank", async () => {
    const { fn, calls } = makeCancelFetch([
      { status: 200, body: REFERENCE },
    ]);
    vi.stubGlobal("fetch", fn);

    render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "  stop now  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(calls[0].body).toEqual({ reason: "stop now" });
  });

  it("reuses the same Idempotency-Key across a network-failure retry, and issues a new key after reopening", async () => {
    const { fn, calls } = makeCancelFetch([
      { networkError: true },
      { status: 200, body: REFERENCE },
    ]);
    vi.stubGlobal("fetch", fn);

    const { rerender } = render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(calls.length).toBe(2);
    expect(calls[0].headers["Idempotency-Key"]).toBe(
      calls[1].headers["Idempotency-Key"],
    );

    rerender(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={false}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );
    rerender(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(calls.length).toBe(3);
    expect(calls[2].headers["Idempotency-Key"]).not.toBe(
      calls[0].headers["Idempotency-Key"],
    );
  });

  it("shows the job_not_cancellable copy on 409 and keeps the dialog open", async () => {
    const { fn } = makeCancelFetch([
      {
        status: 409,
        body: { detail: { code: "job_not_cancellable", status: "succeeded" } },
      },
    ]);
    vi.stubGlobal("fetch", fn);
    const onClose = vi.fn();

    render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={onClose}
        onCancelled={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(
      screen.getByText(
        "This Job is already succeeded and cannot be cancelled.",
      ),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("calls onCancelled with the reference and onClose on success", async () => {
    const { fn } = makeCancelFetch([{ status: 200, body: REFERENCE }]);
    vi.stubGlobal("fetch", fn);
    const onClose = vi.fn();
    const onCancelled = vi.fn();

    render(
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={onClose}
        onCancelled={onCancelled}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(onCancelled).toHaveBeenCalledWith(REFERENCE);
    expect(onClose).toHaveBeenCalled();
  });
});

describe("WR-C-08: CancelJobDialog idempotency key", () => {
  it("opens without throwing when crypto.randomUUID is unavailable and sends a UUID-shaped key", async () => {
    const { fn, calls } = makeCancelFetch([{ status: 200, body: REFERENCE }]);
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
      <CancelJobDialog
        jobId={REFERENCE.job_id}
        jobType="probe_type"
        jobStatus="queued"
        open={true}
        onClose={vi.fn()}
        onCancelled={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(calls[0].headers["Idempotency-Key"]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});

type CancelFirstFrame = {
  reason: string | null;
  errorText: string | null;
  cancelDisabled: boolean | null;
  bodyText: string;
};

/**
 * Sibling probe: its useLayoutEffect runs in the same commit as the dialog,
 * before passive effects, so it sees the first committed frame of an opening
 * (RTL's act() would otherwise flush a passive-effect reset before assertions).
 */
function CancelFrameProbe({
  open,
  frames,
}: {
  open: boolean;
  frames: CancelFirstFrame[];
}) {
  const wasOpen = useRef(false);
  useLayoutEffect(() => {
    if (open && !wasOpen.current) {
      const reason = document.getElementById(
        "cancel-job-dialog-reason",
      ) as HTMLTextAreaElement | null;
      const cancel = Array.from(
        document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button'),
      ).find((b) => b.textContent === "Cancel Job");
      frames.push({
        reason: reason ? reason.value : null,
        errorText: document.querySelector("p.text-red-400")?.textContent ?? null,
        cancelDisabled: cancel ? cancel.disabled : null,
        bodyText: document.body.textContent ?? "",
      });
    }
    wasOpen.current = open;
  });
  return null;
}

describe("UAT gap 3: clean first frame", () => {
  function GapHarness({ frames }: { frames: CancelFirstFrame[] }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          opener
        </button>
        <CancelJobDialog
          jobId={REFERENCE.job_id}
          jobType="probe_type"
          jobStatus="queued"
          open={open}
          onClose={() => setOpen(false)}
          onCancelled={vi.fn()}
        />
        <CancelFrameProbe open={open} frames={frames} />
      </>
    );
  }

  it("re-opens with an empty reason and an enabled Cancel Job after a typed reason plus Keep Job", () => {
    vi.stubGlobal("fetch", makeCancelFetch([{ status: 200, body: REFERENCE }]).fn);
    const frames: CancelFirstFrame[] = [];
    render(<GapHarness frames={frames} />);
    fireEvent.click(screen.getByRole("button", { name: "opener" }));
    fireEvent.change(screen.getByLabelText("Reason (optional)"), {
      target: { value: "old" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Keep Job" }));
    fireEvent.click(screen.getByRole("button", { name: "opener" }));

    expect(frames).toHaveLength(2);
    expect(frames[1].reason).toBe("");
    expect(frames[1].errorText).toBeNull();
    expect(frames[1].cancelDisabled).toBe(false);
  });

  it("re-opens without the previous error text after a failed cancel", async () => {
    const { fn } = makeCancelFetch([
      {
        status: 409,
        body: { detail: { code: "job_not_cancellable", status: "succeeded" } },
      },
    ]);
    vi.stubGlobal("fetch", fn);
    const frames: CancelFirstFrame[] = [];
    render(<GapHarness frames={frames} />);
    fireEvent.click(screen.getByRole("button", { name: "opener" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();
    const previousError = document.querySelector("p.text-red-400")?.textContent;
    expect(previousError).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep Job" }));
    fireEvent.click(screen.getByRole("button", { name: "opener" }));

    expect(frames).toHaveLength(2);
    expect(frames[1].bodyText).not.toContain(previousError as string);
    expect(frames[1].errorText).toBeNull();
    expect(frames[1].cancelDisabled).toBe(false);
  });

  it("uses one Idempotency-Key per opening, differing between openings", async () => {
    const { fn, calls } = makeCancelFetch([{ networkError: true }]);
    vi.stubGlobal("fetch", fn);
    render(<GapHarness frames={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "opener" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Keep Job" }));
    fireEvent.click(screen.getByRole("button", { name: "opener" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await flush();

    expect(calls).toHaveLength(3);
    expect(calls[0].headers["Idempotency-Key"]).toBe(calls[1].headers["Idempotency-Key"]);
    expect(calls[2].headers["Idempotency-Key"]).not.toBe(calls[0].headers["Idempotency-Key"]);
  });
});
