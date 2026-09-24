import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchApi, submitJob, cancelJob } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html" },
  });
}

describe("fetchApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns ok:true with parsed data on a successful JSON response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" })),
    );

    const result = await fetchApi<{ status: string }>("/api/v1/system");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data).toEqual({ status: "ok" });
      expect(result.endpoint).toBe("/api/v1/system");
      expect(result.asOf).toBeInstanceOf(Date);
    }
  });

  it("returns ok:false with status and parsed body on an HTTP error with a JSON body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(503, { detail: "db down" })),
    );

    const result = await fetchApi("/api/v1/system/kill-switch");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(503);
      expect(result.message).toContain("db down");
      expect((result.body as { detail: string }).detail).toBe("db down");
      expect(result.endpoint).toBe("/api/v1/system/kill-switch");
      expect(result.asOf).toBeInstanceOf(Date);
    }
  });

  it("returns ok:false with a meaningful message on an HTTP error with a non-JSON body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(textResponse(502, "<html>Bad Gateway</html>")),
    );

    const result = await fetchApi("/api/v1/system");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(502);
      expect(result.message).toContain("502");
      expect(result.endpoint).toBe("/api/v1/system");
    }
  });

  it("returns ok:false with status:null and never throws on a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    const result = await fetchApi("/api/v1/system");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBeNull();
      expect(result.message.toLowerCase()).toMatch(/unreachable|network|failed/);
      expect(result.endpoint).toBe("/api/v1/system");
      expect(result.asOf).toBeInstanceOf(Date);
    }
  });
});

describe("submitJob / cancelJob", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends method POST, the Idempotency-Key header, Content-Type header, and a JSON body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(202, {
          job_id: "j1",
          job_type: "backtest",
          status: "queued",
          links: { self: "/api/v1/jobs/j1", progress: "", logs: "", events: "" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await submitJob(
      { job_type: "backtest", payload: { strategy_id: "s1" } },
      "key-1",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/v1/jobs");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("key-1");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      job_type: "backtest",
      payload: { strategy_id: "s1" },
    });
  });

  it("returns ok:true replayed:false on a 202 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(202, {
          job_id: "j1",
          job_type: "backtest",
          status: "queued",
          links: { self: "", progress: "", logs: "", events: "" },
        }),
      ),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.replayed).toBe(false);
      expect(result.status).toBe(202);
    }
  });

  it("returns ok:true replayed:true on a 200 with Idempotency-Replayed: true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            job_id: "j1",
            job_type: "backtest",
            status: "queued",
            links: { self: "", progress: "", logs: "", events: "" },
          }),
          {
            status: 200,
            headers: {
              "content-type": "application/json",
              "Idempotency-Replayed": "true",
            },
          },
        ),
      ),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.replayed).toBe(true);
    }
  });

  it("maps 403 mutations_disabled to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(403, { detail: { code: "mutations_disabled" } }),
      ),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.code).toBe("mutations_disabled");
      expect(result.message).toBe("Mutations disabled on this deployment");
    }
  });

  it("maps 422 invalid_job_payload with a reason to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          detail: {
            code: "invalid_job_payload",
            job_type: "backtest",
            reason: "to_date_in_future",
          },
        }),
      ),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "This submission was rejected: to_date_in_future.",
      );
    }
  });

  it("maps 409 idempotency_key_conflict to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: {
            code: "idempotency_key_conflict",
            original_job_id: "j-original",
          },
        }),
      ),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "Idempotency key reused with a different payload — this is a console bug, not an operator error. Reload the page and try again.",
      );
    }
  });

  it("falls back to the generic copy for an unmapped code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(500, { detail: { code: "weird" } })),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "Operation failed (weird). Try again; if it persists, check the API logs.",
      );
    }
  });

  it("returns ok:false with status:null and never throws on a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    const result = await submitJob(
      { job_type: "backtest", payload: {} },
      "key-1",
    );

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBeNull();
      expect(result.code).toBeNull();
    }
  });

  it("posts to /backend/api/v1/jobs/<id>/cancel with body {reason: null} when reason is null", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        job_id: "j1",
        job_type: "backtest",
        status: "cancelled",
        links: { self: "", progress: "", logs: "", events: "" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await cancelJob("j1", null, "key-2");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/v1/jobs/j1/cancel");
    expect(JSON.parse(init.body as string)).toEqual({ reason: null });
  });

  it("interpolates the caller-supplied job id for job_not_found when the server omits detail.job_id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(404, { detail: { code: "job_not_found" } }),
      ),
    );

    const result = await cancelJob("missing-job", null, "key-2");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe("Job missing-job was not found.");
    }
  });
});
