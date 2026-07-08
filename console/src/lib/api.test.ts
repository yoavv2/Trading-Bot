import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchApi } from "./api";

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
