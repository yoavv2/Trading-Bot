// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ReconciliationJobForm } from "./ReconciliationJobForm";
import type { MutationCapability } from "@/lib/useMutationCapability";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type CapturedCall = { headers: Record<string, string>; body: unknown };

/** Routes the single global fetch(): only POST /api/v1/jobs is expected (no strategies GET). */
function makeFetchRouter(responses: Array<{ status: number; body: unknown }>) {
  const calls: CapturedCall[] = [];
  let count = 0;
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/jobs")) {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const body = init?.body ? JSON.parse(init.body as string) : null;
      calls.push({ headers, body });
      const response = responses[Math.min(count, responses.length - 1)];
      count += 1;
      return Promise.resolve(jsonResponse(response.status, response.body));
    }
    throw new Error(`ReconciliationJobForm.test.tsx: unexpected fetch URL ${url}`);
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

const CAPABILITY_ENABLED: MutationCapability = {
  state: "enabled",
  reason: null,
  catalog: null,
  loading: false,
};

const CAPABILITY_DISABLED: MutationCapability = {
  state: "disabled",
  reason: "Mutations disabled on this deployment",
  catalog: null,
  loading: false,
};

const JOB_REFERENCE = {
  job_id: "job-abc-123",
  job_type: "reconciliation",
  status: "queued" as const,
  links: { self: "", progress: "", logs: "", events: "" },
};

const BUTTON = "Submit Reconciliation";

function renderForm(
  capability: MutationCapability = CAPABILITY_ENABLED,
  initialParams: Record<string, string> = {},
  onNavigate: (href: string) => void = vi.fn(),
  submissionDefaults?: Record<string, string>,
) {
  return render(
    <ReconciliationJobForm
      catalogEntry={{
        job_type: "reconciliation",
        description: "d",
        cancellation_mode: "queued_only",
        submission_defaults: submissionDefaults,
      }}
      capability={capability}
      initialParams={initialParams}
      onNavigate={onNavigate}
    />,
  );
}

beforeEach(() => {
  stubRandomUUID();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReconciliationJobForm", () => {
  it("shortcuts and the two forms post scope account: payload is exactly {scope: account}", async () => {
    const { fn, calls } = makeFetchRouter([{ status: 202, body: JOB_REFERENCE }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    renderForm(CAPABILITY_ENABLED, {}, onNavigate);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    await flush();

    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-abc-123");
    expect(calls[0].body).toEqual({ job_type: "reconciliation", payload: { scope: "account" } });
    expect(calls[0].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("adds as_of_session only when the operator enters a date", async () => {
    const { fn, calls } = makeFetchRouter([{ status: 202, body: JOB_REFERENCE }]);
    vi.stubGlobal("fetch", fn);

    renderForm();
    await flush();
    fireEvent.change(screen.getByLabelText("As of session (optional)"), {
      target: { value: "2026-02-02" },
    });
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    await flush();

    expect(calls[0].body).toEqual({
      job_type: "reconciliation",
      payload: { scope: "account", as_of_session: "2026-02-02" },
    });
  });

  it("renders no strategy field, ignores a strategy_id param and a submission default date", async () => {
    const { fn, calls } = makeFetchRouter([{ status: 202, body: JOB_REFERENCE }]);
    vi.stubGlobal("fetch", fn);

    renderForm(CAPABILITY_ENABLED, { strategy_id: "trend_following_daily" }, vi.fn(), {
      as_of_session: "2026-01-05",
    });
    await flush();

    expect(screen.queryByLabelText("Strategy")).toBeNull();
    expect(
      (screen.getByLabelText("As of session (optional)") as HTMLInputElement).value,
    ).toBe("");
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    await flush();
    expect(calls[0].body).toEqual({ job_type: "reconciliation", payload: { scope: "account" } });
    expect(JSON.stringify(calls[0].body)).not.toContain("strategy_id");
  });

  it("uses one Idempotency-Key per opening across a retried submit", async () => {
    const { fn, calls } = makeFetchRouter([
      { status: 503, body: { detail: "unavailable" } },
      { status: 202, body: JOB_REFERENCE },
    ]);
    vi.stubGlobal("fetch", fn);

    renderForm();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    await flush();

    expect(calls.length).toBe(2);
    expect(calls[0].headers["Idempotency-Key"]).toBe(calls[1].headers["Idempotency-Key"]);
  });

  it("disables submit and shows the reason when mutations are disabled", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    renderForm(CAPABILITY_DISABLED);
    await flush();

    expect(
      (screen.getByRole("button", { name: BUTTON }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });
});
