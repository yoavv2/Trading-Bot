// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { JobShortcutLink } from "./JobShortcutLink";
import { PaperJobShortcuts } from "@/components/paper/PaperJobShortcuts";

function stubJobTypes(mutationsEnabled: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/job-types")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ mutations_enabled: mutationsEnabled, items: [] }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      throw new Error(`JobShortcutLink.test.tsx: unexpected fetch URL ${url}`);
    }),
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

describe("JobShortcutLink", () => {
  it("enabled -> anchor to /jobs/new with type and strategy_id", async () => {
    stubJobTypes(true);
    render(
      <JobShortcutLink
        jobType="risk-evaluation"
        strategyId="s"
        label="Evaluate risk"
      />,
    );
    await flush();

    expect(
      screen.getByRole("link", { name: "Evaluate risk" }).getAttribute("href"),
    ).toBe("/jobs/new?type=risk-evaluation&strategy_id=s");
  });

  it("disabled -> disabled button and the capability reason", async () => {
    stubJobTypes(false);
    render(
      <JobShortcutLink
        jobType="risk-evaluation"
        strategyId="s"
        label="Evaluate risk"
      />,
    );
    await flush();

    expect(screen.queryByRole("link", { name: "Evaluate risk" })).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Evaluate risk" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });
});

describe("PaperJobShortcuts", () => {
  it("renders the three paper links with the pinned hrefs", async () => {
    stubJobTypes(true);
    render(<PaperJobShortcuts />);
    await flush();

    const href = (name: string) =>
      screen.getByRole("link", { name }).getAttribute("href");
    expect(href("Run paper session")).toBe(
      "/jobs/new?type=paper-session&strategy_id=trend_following_daily",
    );
    expect(href("Run reconciliation")).toBe(
      "/jobs/new?type=reconciliation&strategy_id=trend_following_daily",
    );
    expect(href("Sync broker orders")).toBe(
      "/jobs/new?type=broker-order-sync&strategy_id=trend_following_daily",
    );
  });
});
