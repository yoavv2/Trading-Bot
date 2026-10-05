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

describe("JobShortcutLink account scope", () => {
  it("scope account -> href has scope=account and no strategy_id", async () => {
    stubJobTypes(true);
    render(
      <JobShortcutLink jobType="reconciliation" scope="account" label="Run reconciliation" />,
    );
    await flush();

    const href = screen
      .getByRole("link", { name: "Run reconciliation" })
      .getAttribute("href");
    expect(href).toBe("/jobs/new?type=reconciliation&scope=account");
    expect(href).not.toContain("strategy_id");
  });

  it("scope account ignores a strategyId and stays disabled when mutations are off", async () => {
    stubJobTypes(false);
    render(
      <JobShortcutLink
        jobType="reconciliation"
        scope="account"
        strategyId="s"
        label="Run reconciliation"
      />,
    );
    await flush();

    expect(screen.queryByRole("link", { name: "Run reconciliation" })).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Run reconciliation" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});

describe("PaperJobShortcuts", () => {
  it("shortcuts and the two forms post scope account: both links carry scope=account", async () => {
    stubJobTypes(true);
    render(<PaperJobShortcuts />);
    await flush();

    const href = (name: string) =>
      screen.getByRole("link", { name }).getAttribute("href");
    expect(href("Run reconciliation")).toBe("/jobs/new?type=reconciliation&scope=account");
    expect(href("Sync broker orders")).toBe("/jobs/new?type=broker-order-sync&scope=account");
  });

  it("no paper-session start control is reachable: no Run paper session text or link, notice shown", async () => {
    stubJobTypes(true);
    const { container } = render(<PaperJobShortcuts />);
    await flush();

    expect(screen.queryByText(/Run paper session/)).toBeNull();
    expect(container.innerHTML).not.toContain("paper-session");
    expect(
      screen.getByText("Paper sessions are operated through the API in this version."),
    ).toBeTruthy();
  });
});
