// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { ActivePaperStrategyLine } from "./ActivePaperStrategyLine";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(response: () => Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => response()),
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ActivePaperStrategyLine", () => {
  it("renders the display name of the owner", async () => {
    stub(() =>
      Promise.resolve(
        jsonResponse(200, { strategy_id: "trend_following_daily", display_name: "Trend Following Daily" }),
      ),
    );
    render(<ActivePaperStrategyLine />);
    await flush();

    expect(
      screen.getByText("Active paper strategy: Trend Following Daily (managed through the API)"),
    ).toBeTruthy();
  });

  it("falls back to the strategy id when no display name is returned", async () => {
    stub(() => Promise.resolve(jsonResponse(200, { strategy_id: "trend_following_daily" })));
    render(<ActivePaperStrategyLine />);
    await flush();

    expect(
      screen.getByText("Active paper strategy: trend_following_daily (managed through the API)"),
    ).toBeTruthy();
  });

  it("active paper strategy line renders none when there is no owner", async () => {
    stub(() => Promise.resolve(jsonResponse(200, { strategy_id: null, display_name: null })));
    render(<ActivePaperStrategyLine />);
    await flush();

    expect(screen.getByText("Active paper strategy: none (managed through the API)")).toBeTruthy();
  });

  it("loading renders an honest loading text, never none", async () => {
    stub(() => new Promise<Response>(() => {}));
    render(<ActivePaperStrategyLine />);
    await flush();

    expect(screen.getByText("Active paper strategy: loading")).toBeTruthy();
    expect(screen.queryByText(/none/)).toBeNull();
  });

  it("a failed fetch renders unknown, never none", async () => {
    stub(() => Promise.resolve(jsonResponse(500, { detail: "boom" })));
    render(<ActivePaperStrategyLine />);
    await flush();

    expect(screen.getByText("Active paper strategy: unknown")).toBeTruthy();
    expect(screen.queryByText(/none/)).toBeNull();
  });

  it("the line's DOM contains no interactive element", async () => {
    stub(() => Promise.resolve(jsonResponse(200, { strategy_id: "s", display_name: "S" })));
    const { container } = render(<ActivePaperStrategyLine />);
    await flush();

    expect(
      container.querySelectorAll(
        "button, a, input, select, textarea, form, [role='button'], [role='link'], [onclick]",
      ).length,
    ).toBe(0);
  });
});
