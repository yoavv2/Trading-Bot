// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BacktestJobForm } from "./BacktestJobForm";
import type { JobTypeCatalogItem } from "../types";
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
 * Routes the console's single global fetch() by URL: GET /api/v1/strategies
 * (queried by BacktestJobForm's own strategy select) and POST /api/v1/jobs
 * (submitJob) are both dispatched through this one stub, matching how the
 * two live calls actually share global fetch at runtime.
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
      throw new Error(`BacktestJobForm.test.tsx: unexpected fetch URL ${url}`);
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

const CATALOG_ENTRY_WITH_DEFAULTS: JobTypeCatalogItem = {
  job_type: "backtest",
  description:
    "Run a historical backtest of a registered strategy over an explicit date range.",
  cancellation_mode: "step_boundary",
  submission_defaults: { from_date: "2026-01-01", to_date: "2026-01-31" },
};

const CATALOG_ENTRY_NO_DEFAULTS: JobTypeCatalogItem = {
  job_type: "backtest",
  description:
    "Run a historical backtest of a registered strategy over an explicit date range.",
  cancellation_mode: "step_boundary",
};

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

const CAPABILITY_UNKNOWN: MutationCapability = {
  state: "unknown",
  reason: "Mutation availability unknown — GET /api/v1/job-types failed",
  catalog: null,
  loading: false,
};

const JOB_REFERENCE_BASE = {
  job_type: "backtest",
  status: "queued" as const,
  links: { self: "", progress: "", logs: "", events: "" },
};

beforeEach(() => {
  stubRandomUUID();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("BacktestJobForm", () => {
  it("pre-fills dates from catalog submission_defaults and strategy from initialParams", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect((screen.getByLabelText("From date") as HTMLInputElement).value).toBe(
      "2026-01-01",
    );
    expect((screen.getByLabelText("To date") as HTMLInputElement).value).toBe(
      "2026-01-31",
    );
    expect(
      (screen.getByLabelText("Strategy") as HTMLSelectElement).value,
    ).toBe("trend_following_daily");
  });

  it("disables submit until all three fields are set; dates start empty without submission_defaults", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_NO_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{}}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect((screen.getByLabelText("From date") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("To date") as HTMLInputElement).value).toBe("");
    expect(
      (screen.getByRole("button", { name: "Submit Backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "trend_following_daily" },
    });
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-02-01" },
    });
    expect(
      (screen.getByRole("button", { name: "Submit Backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-02-28" },
    });
    expect(
      (screen.getByRole("button", { name: "Submit Backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("202 navigates to /jobs/<job_id> with the exact request body", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-abc-123" };
    const { fn, calls } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={onNavigate}
      />,
    );
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Submit Backtest" }));
    await flush();

    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-abc-123");
    expect(calls[0].body).toEqual({
      job_type: "backtest",
      payload: {
        strategy_id: "trend_following_daily",
        from_date: "2026-01-01",
        to_date: "2026-01-31",
      },
    });
  });

  it("200 with Idempotency-Replayed: true shows the replay copy and navigates", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-replayed-1" };
    const { fn } = makeFetchRouter([
      {
        status: 200,
        body: jobReference,
        headers: { "Idempotency-Replayed": "true" },
      },
    ]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={onNavigate}
      />,
    );
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Submit Backtest" }));
    await flush();

    expect(
      screen.getByText("Already submitted — opening existing Job"),
    ).toBeTruthy();
    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-replayed-1");
  });

  it("409 idempotency_key_conflict shows the conflict copy and does not navigate", async () => {
    const { fn } = makeFetchRouter([
      { status: 409, body: { detail: { code: "idempotency_key_conflict" } } },
    ]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={onNavigate}
      />,
    );
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Submit Backtest" }));
    await flush();

    expect(
      screen.getByText(
        "Idempotency key reused with a different payload — this is a console bug, not an operator error. Reload the page and try again.",
      ),
    ).toBeTruthy();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("reuses the same Idempotency-Key across a network-failure retry with an identical payload, and rotates it when the payload changes", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-retry-1" };
    const { fn, calls } = makeFetchRouter([
      { networkError: true },
      { status: 202, body: jobReference },
    ]);
    vi.stubGlobal("fetch", fn);

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Submit Backtest" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Submit Backtest" }));
    await flush();

    expect(calls.length).toBe(2);
    expect(calls[0].headers["Idempotency-Key"]).toBe(
      calls[1].headers["Idempotency-Key"],
    );

    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-02-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit Backtest" }));
    await flush();

    expect(calls.length).toBe(3);
    expect(calls[2].headers["Idempotency-Key"]).not.toBe(
      calls[0].headers["Idempotency-Key"],
    );
  });

  it("disables submit and shows the D-21 reason when mutations are disabled", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_DISABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (screen.getByRole("button", { name: "Submit Backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });

  it("disables submit and shows the honest-unknown reason when capability is unknown", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <BacktestJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_UNKNOWN}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (screen.getByRole("button", { name: "Submit Backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getByText("Mutation availability unknown — GET /api/v1/job-types failed"),
    ).toBeTruthy();
  });
});
