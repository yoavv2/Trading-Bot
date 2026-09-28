import { afterEach, describe, expect, it, vi } from "vitest";
import { newIdempotencyKey } from "./idempotencyKey";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("newIdempotencyKey", () => {
  it("uses crypto.randomUUID when it is available", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "fixed-uuid" });
    expect(newIdempotencyKey()).toBe("fixed-uuid");
  });

  it("falls back to a v4 UUID from getRandomValues when randomUUID is missing (insecure context)", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.forEach((_, index) => {
          bytes[index] = index * 17;
        });
        return bytes;
      },
    });
    const key = newIdempotencyKey();
    expect(key).toMatch(UUID_V4);
  });

  it("falls back to Math.random when no Web Crypto exists, still UUID-shaped and unique", () => {
    vi.stubGlobal("crypto", undefined);
    const first = newIdempotencyKey();
    const second = newIdempotencyKey();
    expect(first).toMatch(UUID_V4);
    expect(second).toMatch(UUID_V4);
    expect(first).not.toBe(second);
  });
});
