// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PaperSessionJobForm } from "./PaperSessionJobForm";
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
 * (queried by the shared StrategySelectField) and POST /api/v1/jobs
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
      throw new Error(`PaperSessionJobForm.test.tsx: unexpected fetch URL ${url}`);
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
  job_type: "paper-session",
  description:
    "Run the daily paper-trading session (reconcile, correct, submit orders) for one strategy and session. Cancellable only while queued; once running, the session runs to completion.",
  cancellation_mode: "queued_only",
  submission_defaults: { as_of_session: "2026-01-05" },
};

const CATALOG_ENTRY_NO_DEFAULTS: JobTypeCatalogItem = {
  job_type: "paper-session",
  description:
    "Run the daily paper-trading session (reconcile, correct, submit orders) for one strategy and session. Cancellable only while queued; once running, the session runs to completion.",
  cancellation_mode: "queued_only",
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

const JOB_REFERENCE_BASE = {
  job_type: "paper-session",
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

describe("PaperSessionJobForm", () => {
  it("pre-fills as_of_session from catalog submission_defaults and strategy from initialParams; risk run field starts blank", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <PaperSessionJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (screen.getByLabelText("As of session") as HTMLInputElement).value,
    ).toBe("2026-01-05");
    expect(
      (screen.getByLabelText("Strategy") as HTMLSelectElement).value,
    ).toBe("trend_following_daily");
    expect(
      (screen.getByLabelText("Risk run ID (optional)") as HTMLInputElement)
        .value,
    ).toBe("");
    expect(
      screen.getByText(
        "Leave blank to use the latest succeeded risk evaluation.",
      ),
    ).toBeTruthy();
  });

  it("disables submit until strategy and as_of_session are set; as_of_session starts empty without submission_defaults", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <PaperSessionJobForm
        catalogEntry={CATALOG_ENTRY_NO_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{}}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (screen.getByLabelText("As of session") as HTMLInputElement).value,
    ).toBe("");
    expect(
      (
        screen.getByRole("button", {
          name: "Submit Paper Session",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("Strategy"), {
      target: { value: "trend_following_daily" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Submit Paper Session",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("As of session"), {
      target: { value: "2026-02-02" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Submit Paper Session",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });

  it("202 posts risk_run_id: null (key present, JSON null) when the field is left blank", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-abc-123" };
    const { fn, calls } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <PaperSessionJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={onNavigate}
      />,
    );
    await flush();

    fireEvent.click(
      screen.getByRole("button", { name: "Submit Paper Session" }),
    );
    await flush();

    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-abc-123");
    expect(calls[0].body).toEqual({
      job_type: "paper-session",
      payload: {
        strategy_id: "trend_following_daily",
        as_of_session: "2026-01-05",
        risk_run_id: null,
      },
    });
    expect(
      Object.prototype.hasOwnProperty.call(
        (calls[0].body as { payload: Record<string, unknown> }).payload,
        "risk_run_id",
      ),
    ).toBe(true);
    expect(calls[0].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("sends the trimmed risk run ID string when the field is filled", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-def-456" };
    const { fn, calls } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <PaperSessionJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={onNavigate}
      />,
    );
    await flush();

    fireEvent.change(screen.getByLabelText("Risk run ID (optional)"), {
      target: { value: "  11111111-1111-4111-8111-111111111111  " },
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Submit Paper Session" }),
    );
    await flush();

    expect(calls[0].body).toEqual({
      job_type: "paper-session",
      payload: {
        strategy_id: "trend_following_daily",
        as_of_session: "2026-01-05",
        risk_run_id: "11111111-1111-4111-8111-111111111111",
      },
    });
  });

  it("disables submit and shows the reason when mutations are disabled", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <PaperSessionJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_DISABLED}
        initialParams={{ strategy_id: "trend_following_daily" }}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (
        screen.getByRole("button", {
          name: "Submit Paper Session",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });
});
