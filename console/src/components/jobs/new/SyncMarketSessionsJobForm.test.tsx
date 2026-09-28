// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SyncMarketSessionsJobForm } from "./SyncMarketSessionsJobForm";
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

type JobsFetchResponse =
  | { networkError: true }
  | { status: number; body: unknown; headers?: Record<string, string> };

type CapturedCall = {
  headers: Record<string, string>;
  body: unknown;
};

/**
 * Routes the console's single global fetch() by URL: POST /api/v1/jobs
 * (submitJob) is the only live call this form makes (no StrategySelectField).
 */
function makeFetchRouter(jobsResponses: JobsFetchResponse[]) {
  const calls: CapturedCall[] = [];
  let jobsCallCount = 0;

  const fn = vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
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
      throw new Error(
        `SyncMarketSessionsJobForm.test.tsx: unexpected fetch URL ${url}`,
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

const CATALOG_ENTRY_WITH_DEFAULTS: JobTypeCatalogItem = {
  job_type: "sync-market-sessions",
  description: "Upsert exchange trading-session rows for an explicit date range.",
  cancellation_mode: "step_boundary",
  submission_defaults: {
    from_date: "2025-01-01",
    to_date: "2026-01-01",
  },
};

const CATALOG_ENTRY_NO_DEFAULTS: JobTypeCatalogItem = {
  job_type: "sync-market-sessions",
  description: "Upsert exchange trading-session rows for an explicit date range.",
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

const JOB_REFERENCE_BASE = {
  job_type: "sync-market-sessions",
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

describe("SyncMarketSessionsJobForm", () => {
  it("pre-fills from/to dates from catalog submission_defaults", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <SyncMarketSessionsJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{}}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (screen.getByLabelText("From date") as HTMLInputElement).value,
    ).toBe("2025-01-01");
    expect((screen.getByLabelText("To date") as HTMLInputElement).value).toBe(
      "2026-01-01",
    );
  });

  it("submits payload.{from_date, to_date} and navigates to the new Job", async () => {
    const jobReference = { ...JOB_REFERENCE_BASE, job_id: "job-abc-123" };
    const { fn, calls } = makeFetchRouter([{ status: 202, body: jobReference }]);
    vi.stubGlobal("fetch", fn);
    const onNavigate = vi.fn();

    render(
      <SyncMarketSessionsJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{}}
        onNavigate={onNavigate}
      />,
    );
    await flush();

    fireEvent.click(
      screen.getByRole("button", { name: "Submit Sync Market Sessions" }),
    );
    await flush();

    expect(onNavigate).toHaveBeenCalledWith("/jobs/job-abc-123");
    expect(calls[0].body).toEqual({
      job_type: "sync-market-sessions",
      payload: { from_date: "2025-01-01", to_date: "2026-01-01" },
    });
    expect(calls[0].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("disables submit until both dates are set; fields start empty without submission_defaults", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <SyncMarketSessionsJobForm
        catalogEntry={CATALOG_ENTRY_NO_DEFAULTS}
        capability={CAPABILITY_ENABLED}
        initialParams={{}}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect((screen.getByLabelText("From date") as HTMLInputElement).value).toBe(
      "",
    );
    expect((screen.getByLabelText("To date") as HTMLInputElement).value).toBe(
      "",
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Submit Sync Market Sessions",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2025-01-01" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Submit Sync Market Sessions",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);

    fireEvent.change(screen.getByLabelText("To date"), {
      target: { value: "2026-01-01" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Submit Sync Market Sessions",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });

  it("disables submit and shows the reason when mutations are disabled", async () => {
    const { fn } = makeFetchRouter([]);
    vi.stubGlobal("fetch", fn);

    render(
      <SyncMarketSessionsJobForm
        catalogEntry={CATALOG_ENTRY_WITH_DEFAULTS}
        capability={CAPABILITY_DISABLED}
        initialParams={{}}
        onNavigate={vi.fn()}
      />,
    );
    await flush();

    expect(
      (
        screen.getByRole("button", {
          name: "Submit Sync Market Sessions",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });
});
