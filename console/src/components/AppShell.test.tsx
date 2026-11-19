// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

vi.mock("next/navigation", () => ({ usePathname: () => "/research/strategies/new" }));
afterEach(() => { cleanup(); localStorage.clear(); delete document.documentElement.dataset.theme; });

describe("Research app shell", () => {
  it("keeps research destinations primary and system activity accessible separately", () => {
    render(<AppShell><h1>Build a strategy</h1></AppShell>);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(nav.textContent).toBe("DashboardStrategiesAsset lists");
    expect(screen.getByRole("link", { name: "Strategies" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: /System activity/ }).getAttribute("href")).toBe("/jobs");
    expect(screen.getByRole("link", { name: "Skip to content" }).getAttribute("href")).toBe("#main-content");
  });
  it("restores a saved theme and persists the user's next choice", () => {
    localStorage.setItem("research-theme", "dark");
    render(<AppShell>Research</AppShell>);
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("research-theme")).toBe("light");
  });
});
