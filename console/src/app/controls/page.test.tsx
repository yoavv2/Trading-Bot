// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import ControlsPage from "./page";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("/controls page", () => {
  it("renders the Controls heading, kill-switch trigger and strategy trigger from one page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/v1/system/kill-switch")) {
          return Promise.resolve(
            jsonResponse({
              name: "global",
              state: "armed",
              is_tripped: false,
              last_changed_at: "2026-09-28T00:00:00Z",
              last_change_actor: null,
              last_change_reason: null,
              last_change_run_id: null,
            }),
          );
        }
        if (url.includes("/api/v1/controls/active-paper-strategy")) {
          return Promise.resolve(
            jsonResponse({
              strategy_id: null,
              display_name: null,
              trading_blocked_reasons: ["no_active_paper_strategy"],
            }),
          );
        }
        if (url.includes("/api/v1/controls/strategies/")) {
          return Promise.resolve(
            jsonResponse({
              strategy_id: "trend_following_daily",
              status: "enabled",
              updated_at: null,
            }),
          );
        }
        return Promise.resolve(jsonResponse({ mutations_enabled: true, items: [] }));
      }),
    );

    render(<ControlsPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { level: 1, name: "Controls" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Trip Kill Switch" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Disable Strategy" })).toBeTruthy();
    expect(
      screen.getByText("Active paper strategy: none (managed through the API)"),
    ).toBeTruthy();
  });
});
