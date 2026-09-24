// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useApiQuery } from "./useApiQuery";
import { mutationCapabilityFrom } from "./useMutationCapability";
import type { ApiResult } from "./api";
import type { JobTypesCatalog } from "../components/jobs/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeSequencedFetch(bodies: unknown[]) {
  let call = 0;
  return vi.fn().mockImplementation(() => {
    const body = bodies[Math.min(call, bodies.length - 1)];
    call += 1;
    return Promise.resolve(jsonResponse(200, body));
  });
}

function setDocumentHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => hidden,
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  setDocumentHidden(false);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useApiQuery polling (JOBUI-05)", () => {
  it("fetches exactly once on mount with no options, and polling is false", async () => {
    const fetchMock = makeSequencedFetch([{ status: "running" }]);
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useApiQuery<{ status: string }>("/api/v1/x"),
    );

    await advance(0);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.polling).toBe(false);

    await advance(10000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("polls every pollIntervalMs while shouldPoll holds and stops the instant it returns false", async () => {
    const fetchMock = makeSequencedFetch([
      { status: "running" },
      { status: "running" },
      { status: "succeeded" },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useApiQuery<{ status: string }>("/api/v1/x", {
        pollIntervalMs: 3000,
        shouldPoll: (data) => data.status !== "succeeded",
      }),
    );

    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.polling).toBe(true);

    await advance(3000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.polling).toBe(true);

    await advance(3000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.current.polling).toBe(false);
    expect(result.current.result?.ok && result.current.result.data.status).toBe(
      "succeeded",
    );

    await advance(9000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("never flips loading to true during background ticks", async () => {
    const fetchMock = makeSequencedFetch([
      { status: "running" },
      { status: "running" },
    ]);
    vi.stubGlobal("fetch", fetchMock);
    const seenLoading: boolean[] = [];

    renderHook(() => {
      const state = useApiQuery<{ status: string }>("/api/v1/x", {
        pollIntervalMs: 1000,
      });
      seenLoading.push(state.loading);
      return state;
    });

    await advance(0);
    seenLoading.length = 0; // only observe post-mount-resolution values

    await advance(1000);

    expect(seenLoading.length).toBeGreaterThan(0);
    expect(seenLoading.every((value) => value === false)).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("pauses while document.hidden is true and resumes with one immediate fetch on visibilitychange", async () => {
    setDocumentHidden(true);
    const fetchMock = makeSequencedFetch([
      { status: "running" },
      { status: "running" },
    ]);
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() =>
      useApiQuery<{ status: string }>("/api/v1/x", { pollIntervalMs: 1000 }),
    );

    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(1); // the mount fetch always runs

    await advance(10000);
    expect(fetchMock).toHaveBeenCalledTimes(1); // no background tick while hidden

    setDocumentHidden(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the previous result and keeps polling after a failed background tick", async () => {
    let call = 0;
    const fetchMock = vi.fn().mockImplementation(() => {
      call += 1;
      if (call === 2) {
        return Promise.resolve(
          new Response("Internal Server Error", {
            status: 500,
            headers: { "content-type": "text/plain" },
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, { status: "running" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useApiQuery<{ status: string }>("/api/v1/x", { pollIntervalMs: 1000 }),
    );

    await advance(0);
    expect(result.current.result?.ok).toBe(true);

    await advance(1000); // this tick fails (500)
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.result?.ok).toBe(true); // previous success preserved
    expect(result.current.polling).toBe(true); // polling continues regardless

    await advance(1000); // proves the chain kept scheduling after the failure
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("mutationCapabilityFrom", () => {
  const asOf = new Date();

  it("returns enabled with a null reason when the catalog reports mutations_enabled true", () => {
    const okResult: ApiResult<JobTypesCatalog> = {
      ok: true,
      data: { mutations_enabled: true, items: [] },
      endpoint: "/api/v1/job-types",
      asOf,
    };

    expect(mutationCapabilityFrom(okResult)).toEqual({
      state: "enabled",
      reason: null,
      catalog: okResult.data,
    });
  });

  it("returns disabled with the D-21 reason when mutations_enabled is false", () => {
    const okResult: ApiResult<JobTypesCatalog> = {
      ok: true,
      data: { mutations_enabled: false, items: [] },
      endpoint: "/api/v1/job-types",
      asOf,
    };

    expect(mutationCapabilityFrom(okResult)).toEqual({
      state: "disabled",
      reason: "Mutations disabled on this deployment",
      catalog: okResult.data,
    });
  });

  it("returns unknown with the honest-unknown reason on a failed fetch or before any result exists", () => {
    const failed: ApiResult<JobTypesCatalog> = {
      ok: false,
      endpoint: "/api/v1/job-types",
      status: 500,
      message: "HTTP 500",
      asOf,
    };

    expect(mutationCapabilityFrom(failed)).toEqual({
      state: "unknown",
      reason: "Mutation availability unknown — GET /api/v1/job-types failed",
      catalog: null,
    });
    expect(mutationCapabilityFrom(null)).toEqual({
      state: "unknown",
      reason: "Mutation availability unknown — GET /api/v1/job-types failed",
      catalog: null,
    });
  });
});
