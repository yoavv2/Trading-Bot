// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  useJobFormSubmission,
  JobFormFooter,
  StrategySelectField,
  useStrategySelection,
  parseSymbolsInput,
  SYMBOLS_EMPTY_HELP,
} from "./jobFormKit";
import type { MutationCapability } from "@/lib/useMutationCapability";

function jsonResponse(
  status: number,
  body: unknown,
  headers?: Record<string, string>,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...(headers ?? {}) },
  });
}

const STRATEGIES_BODY = {
  count: 1,
  strategies: [
    { strategy_id: "trend_following_daily", display_name: "Trend Following Daily" },
  ],
};

type JobsFetchResponse =
  | { networkError: true }
  | { status: number; body: unknown; headers?: Record<string, string> };

type CapturedCall = {
  headers: Record<string, string>;
  body: unknown;
};

/**
 * Routes the console's single global fetch() by URL, matching how a real
 * form shares global fetch between StrategySelectField's own
 * GET /api/v1/strategies query and useJobFormSubmission's POST /api/v1/jobs.
 */
function makeFetchRouter(jobsResponses: JobsFetchResponse[]) {
  const calls: CapturedCall[] = [];
  let jobsCallCount = 0;

  const fn = vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/backend/api/v1/strategies")) {
        return Promise.resolve(jsonResponse(200, STRATEGIES_BODY));
      }
      if (url.includes("/backend/api/v1/jobs")) {
        const headers = (init?.headers ?? {}) as Record<string, string>;
        const body = init?.body ? JSON.parse(init.body as string) : null;
        calls.push({ headers, body });
        const response =
          jobsResponses[Math.min(jobsCallCount, jobsResponses.length - 1)];
        jobsCallCount += 1;
        if ("networkError" in response) {
          return Promise.reject(new Error("network down"));
        }
        return Promise.resolve(
          jsonResponse(response.status, response.body, response.headers),
        );
      }
      throw new Error(`jobFormKit.test.tsx: unexpected fetch URL ${url}`);
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

const JOB_REFERENCE_BASE = {
  job_type: "risk-evaluation",
  status: "queued" as const,
  links: { self: "", progress: "", logs: "", events: "" },
};

/** Minimal test harness composing the kit's hook + footer, mirroring how a
 * real form (e.g. RiskEvaluationJobForm) would wire them together. */
function TestForm({
  payload,
  onNavigate,
  capability = CAPABILITY_ENABLED,
}: {
  payload: Record<string, unknown>;
  onNavigate: (href: string) => void;
  capability?: MutationCapability;
}) {
  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "risk-evaluation",
    onNavigate,
  });
  const canSubmit = capability.state === "enabled" && !submitting;
  return (
    <JobFormFooter
      capability={capability}
      outcome={outcome}
      canSubmit={canSubmit}
      submitting={submitting}
      label="Submit Risk Evaluation"
      onSubmit={() => void submit(payload)}
    />
  );
}

beforeEach(() => {
  stubRandomUUID();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useJobFormSubmission", () => {
  it("submit(payload) calls submitJob with the given jobType and payload", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-1" };
    const { fn, calls } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(<TestForm payload={{ strategy_id: "s1" }} onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();

    expect(calls[0].body).toEqual({
      job_type: "risk-evaluation",
      payload: { strategy_id: "s1" },
    });
    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-1");
  });

  it("reuses the same Idempotency-Key across a network-failure retry with an identical payload, and rotates it when the payload changes", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-2" };
    const { fn, calls } = makeFetchRouter([
      { networkError: true },
      { status: 202, body: jobReference },
    ]);
    vi.stubGlobal("fetch", fn);

    const { rerender } = render(
      <TestForm payload={{ strategy_id: "s1" }} onNavigate={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();

    expect(calls.length).toBe(2);
    expect(calls[0].headers["Idempotency-Key"]).toBe(
      calls[1].headers["Idempotency-Key"],
    );

    rerender(
      <TestForm payload={{ strategy_id: "s2" }} onNavigate={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();

    expect(calls.length).toBe(3);
    expect(calls[2].headers["Idempotency-Key"]).not.toBe(
      calls[0].headers["Idempotency-Key"],
    );
  });

  it("200 with Idempotency-Replayed: true sets outcome replayed and navigates", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-3" };
    const { fn } = makeFetchRouter([
      { status: 200, body: jobReference, headers: { "Idempotency-Replayed": "true" } },
    ]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(<TestForm payload={{ strategy_id: "s1" }} onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();

    expect(
      screen.getByText("Already submitted — opening existing Job"),
    ).toBeTruthy();
    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-3");
  });

  it("an error response sets outcome to {kind: error, message} and does not navigate", async () => {
    const { fn } = makeFetchRouter([
      { status: 409, body: { detail: { code: "idempotency_key_conflict" } } },
    ]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(<TestForm payload={{ strategy_id: "s1" }} onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();

    expect(
      screen.getByText(
        "Idempotency key reused with a different payload — this is a console bug, not an operator error. Reload the page and try again.",
      ),
    ).toBeTruthy();
    expect(onNavigate).not.toHaveBeenCalled();
  });
});

describe("JobFormFooter", () => {
  it("renders capability.reason when state is not enabled, and disables the labeled button", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <TestForm
        payload={{ strategy_id: "s1" }}
        onNavigate={vi.fn()}
        capability={CAPABILITY_DISABLED}
      />,
    );

    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Submit Risk Evaluation" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});

describe("StrategySelectField", () => {
  it("renders a Strategy label/select fed by useStrategySelection(/api/v1/strategies)", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);
    const onChange = vi.fn();

    function Harness() {
      const { strategyId, strategies } = useStrategySelection(undefined);
      return (
        <StrategySelectField
          id="rf-strategy-id"
          value={strategyId}
          strategies={strategies}
          onChange={onChange}
        />
      );
    }
    render(<Harness />);
    await flush();

    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "trend_following_daily" },
    });
    expect(onChange).toHaveBeenCalledWith("trend_following_daily");
    expect(screen.getByText("Trend Following Daily (trend_following_daily)")).toBeTruthy();
  });
});

describe("parseSymbolsInput", () => {
  it("trims, upper-cases, drops empties, de-duplicates, and sorts", () => {
    expect(parseSymbolsInput(" spy, aapl ,SPY,, ")).toEqual(["AAPL", "SPY"]);
  });

  it("returns an empty array for an empty string", () => {
    expect(parseSymbolsInput("")).toEqual([]);
  });

  it("exports the SYMBOLS_EMPTY_HELP copy", () => {
    expect(SYMBOLS_EMPTY_HELP).toBe("At least one symbol is required");
  });
});

describe("WR-C-08: idempotency key generation", () => {
  it("does not generate a key on render or re-render, only once on first submit", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-k" };
    const { fn } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);

    const { rerender } = render(
      <TestForm payload={{ strategy_id: "s1" }} onNavigate={vi.fn()} />,
    );
    rerender(<TestForm payload={{ strategy_id: "s1" }} onNavigate={vi.fn()} />);
    rerender(<TestForm payload={{ strategy_id: "s1" }} onNavigate={vi.fn()} />);
    expect(uuidCounter).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();
    expect(uuidCounter).toBe(1);
  });

  it("mounts and submits with a UUID-shaped key when crypto.randomUUID is unavailable", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-f" };
    const { fn, calls } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.forEach((_, index) => {
          bytes[index] = (index * 31 + 7) % 256;
        });
        return bytes;
      },
    });

    render(<TestForm payload={{ strategy_id: "s1" }} onNavigate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Submit Risk Evaluation" }));
    await flush();

    expect(calls[0].headers["Idempotency-Key"]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
