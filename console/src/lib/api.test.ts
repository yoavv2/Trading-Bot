import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchApi,
  submitJob,
  cancelJob,
  retryJob,
  tripKillSwitch,
  resetKillSwitch,
  enableStrategy,
  disableStrategy,
  isOutcomeUncertain,
} from "./api";

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

  it("maps 409 job_not_cancellable_running to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, { detail: { code: "job_not_cancellable_running" } }),
      ),
    );

    const result = await cancelJob("job-1", null, "key-2");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe("Not cancellable once running");
    }
  });
});

describe("retryJob", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends method POST with the Idempotency-Key header to /backend/api/v1/jobs/<id>/retry", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(202, {
        job_id: "j2",
        job_type: "backtest",
        status: "queued",
        links: { self: "", progress: "", logs: "", events: "" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await retryJob("abc", "key-3");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/v1/jobs/abc/retry");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("key-3");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.replayed).toBe(false);
    }
  });

  it("returns ok:true replayed:true on a 200 with Idempotency-Replayed: true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            job_id: "j2",
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

    const result = await retryJob("abc", "key-3");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.replayed).toBe(true);
    }
  });

  it("maps 409 reconciliation_required to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: {
            code: "reconciliation_required",
            required_job_type: "reconciliation",
            strategy_id: "trend_following_daily",
          },
        }),
      ),
    );

    const result = await retryJob("abc", "key-3");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry.",
      );
    }
  });

  it("maps 409 retry_exists to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: { code: "retry_exists", existing_retry_job_id: "j3" },
        }),
      ),
    );

    const result = await retryJob("abc", "key-3");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe("This Job already has a retry.");
    }
  });

  it("maps 409 job_not_retryable to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, { detail: { code: "job_not_retryable", status: "queued" } }),
      ),
    );

    const result = await retryJob("abc", "key-3");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "This Job cannot be retried — retry is only available for FAILED or CANCELLED Jobs.",
      );
    }
  });

  it("maps 422 invalid_retry_payload with a reason to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, {
          detail: { code: "invalid_retry_payload", reason: "strategy no longer exists" },
        }),
      ),
    );

    const result = await retryJob("abc", "key-3");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "This Job can no longer be retried: strategy no longer exists.",
      );
    }
  });

  it("maps 422 invalid_retry_payload without a usable reason to the fallback UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, { detail: { code: "invalid_retry_payload", reason: "   " } }),
      ),
    );

    const result = await retryJob("abc", "key-3");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "This Job can no longer be retried: the original payload is no longer valid.",
      );
    }
  });
});

describe("control mutations (tripKillSwitch/resetKillSwitch/enableStrategy/disableStrategy)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("tripKillSwitch sends PUT with no Idempotency-Key header and the tripped body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { state: "tripped", changed: true, run_id: "r1" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await tripKillSwitch("drill");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/v1/controls/kill-switch");
    expect(init.method).toBe("PUT");
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["Idempotency-Key"]).toBeUndefined();
    expect(JSON.parse(init.body as string)).toEqual({
      state: "tripped",
      reason: "drill",
    });
    expect(result.ok).toBe(true);
  });

  it("resetKillSwitch sends the armed body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { state: "armed", changed: true, run_id: "r2" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await resetKillSwitch("resuming");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      state: "armed",
      reason: "resuming",
    });
  });

  it("enableStrategy sends PUT /controls/strategies/<id> with the enabled body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        strategy_id: "trend_following_daily",
        status: "enabled",
        changed: true,
        run_id: "r3",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await enableStrategy("trend_following_daily", "r");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/v1/controls/strategies/trend_following_daily");
    expect(JSON.parse(init.body as string)).toEqual({
      status: "enabled",
      reason: "r",
    });
  });

  it("disableStrategy sends the disabled body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        strategy_id: "trend_following_daily",
        status: "disabled",
        changed: true,
        run_id: "r4",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await disableStrategy("trend_following_daily", "r");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      status: "disabled",
      reason: "r",
    });
  });

  it("returns ok:true with data.changed === false on a 200 unchanged response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(200, { state: "tripped", changed: false, run_id: "x" }),
      ),
    );

    const result = await tripKillSwitch("drill");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.changed).toBe(false);
    }
  });

  it("maps 422 invalid_control_reason to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, { detail: { code: "invalid_control_reason" } }),
      ),
    );

    const result = await tripKillSwitch("");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "Reason is required and must be 500 characters or fewer.",
      );
    }
  });

  it("maps 404 strategy_not_found (structured detail) to the exact UI-SPEC copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(404, { detail: { code: "strategy_not_found", strategy_id: "x" } }),
      ),
    );

    const result = await enableStrategy("x", "r");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe("Strategy x was not found.");
    }
  });

  it("renders a plain-string detail verbatim", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(404, { detail: "Unknown strategy 'x'." }),
      ),
    );

    const result = await enableStrategy("x", "r");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe("Unknown strategy 'x'.");
    }
  });

  it("falls back to the defensive copy when detail is an array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, { detail: [{ loc: ["body", "status"], msg: "bad" }] }),
      ),
    );

    const result = await enableStrategy("x", "r");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "Request rejected — check the input and try again.",
      );
    }
  });

  it("maps 422 invalid_control_request to the defensive copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(422, { detail: { code: "invalid_control_request" } }),
      ),
    );

    const result = await enableStrategy("x", "r");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(
        "Request rejected — check the input and try again.",
      );
    }
  });

  it("maps 403 mutations_disabled to the reused copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(403, { detail: { code: "mutations_disabled" } }),
      ),
    );

    const result = await tripKillSwitch("drill");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe("Mutations disabled on this deployment");
    }
  });

  it("returns ok:false with status:null and never throws on a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    const result = await tripKillSwitch("drill");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBeNull();
    }
  });
});

describe("WR-C-03: a 2xx response with an unreadable or wrongly shaped body is a failure", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const UNREADABLE_SUFFIX = "returned an unreadable response. Reload and verify the current state.";

  it("putJson: a 200 with a non-JSON body resolves ok:false with unreadable-response copy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(textResponse(200, "<html>proxy</html>")));

    const result = await tripKillSwitch("drill");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(200);
      expect(result.code).toBeNull();
      expect(result.message).toBe(`/api/v1/controls/kill-switch ${UNREADABLE_SUFFIX}`);
      expect(isOutcomeUncertain(result)).toBe(true);
    }
  });

  it("putJson: a 200 JSON body without a boolean `changed` resolves ok:false", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { state: "tripped" })));

    const result = await tripKillSwitch("drill");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toContain(UNREADABLE_SUFFIX);
    }
  });

  it("putJson: a well-formed 200 body is still ok:true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { state: "tripped", changed: false, run_id: "r" })),
    );

    const result = await tripKillSwitch("drill");

    expect(result.ok).toBe(true);
  });

  it("postJson: a 202 with a non-JSON body resolves ok:false instead of ok:true with null data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(textResponse(202, "accepted")));

    const result = await submitJob({ job_type: "backtest", payload: {} }, "key-1");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toBe(`/api/v1/jobs ${UNREADABLE_SUFFIX}`);
    }
  });

  it("postJson: a 202 JSON body without a job_id resolves ok:false", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(202, {})));

    const result = await retryJob("job-1", "key-1");

    expect(result.ok).toBe(false);
  });

  it("isOutcomeUncertain is true for transport failures, 5xx and unreadable 2xx, false for typed 4xx", () => {
    const failure = (status: number | null) =>
      ({ ok: false, status, code: null, message: "m", detail: null }) as const;
    expect(isOutcomeUncertain(failure(null))).toBe(true);
    expect(isOutcomeUncertain(failure(500))).toBe(true);
    expect(isOutcomeUncertain(failure(503))).toBe(true);
    expect(isOutcomeUncertain(failure(200))).toBe(true);
    expect(isOutcomeUncertain(failure(409))).toBe(false);
    expect(isOutcomeUncertain(failure(422))).toBe(false);
  });
});
