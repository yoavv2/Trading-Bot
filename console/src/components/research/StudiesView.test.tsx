// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { StudiesView } from "./StudiesView";

const query = vi.hoisted(() => vi.fn());

vi.mock("@/lib/useApiQuery", () => ({ useApiQuery: query }));

afterEach(() => {
  cleanup();
  query.mockReset();
});

function ok<T>(data: T) {
  return { ok: true as const, data, asOf: new Date("2026-10-08T12:00:00Z") };
}

describe("StudiesView overview", () => {
  it("shows real workspace counts and recent study links", () => {
    query.mockImplementation((endpoint: string) => {
      if (endpoint.includes("asset-lists")) return { result: ok({ as_of: "", count: 2, lists: [] }), loading: false, refetch: vi.fn() };
      if (endpoint.endsWith("/strategies/drafts")) return { result: ok({ as_of: "", count: 3 }), loading: false, refetch: vi.fn() };
      if (endpoint.endsWith("/strategies")) return { result: ok({ as_of: "", count: 4 }), loading: false, refetch: vi.fn() };
      return { result: ok({ as_of: "", count: 2, studies: [
        { study_id: "study-1", name: "Momentum review", kind: "substantive", created_at: "2026-10-01T00:00:00Z" },
        { study_id: "study-2", name: "Older review", kind: "smoke", created_at: "2025-10-01T00:00:00Z" },
      ] }), loading: false, refetch: vi.fn() };
    });

    render(<StudiesView />);

    expect(screen.getByRole("heading", { name: "Your research, at a glance." })).toBeTruthy();
    expect(screen.getByText("Saved research studies").previousElementSibling?.textContent).toBe("2");
    expect(screen.getByText("Reusable selections for new studies").previousElementSibling?.textContent).toBe("2");
    const studyLink = screen.getByRole("link", { name: /Momentum review/ });
    expect(studyLink.getAttribute("href")).toBe("/research/studies/study-1");
    expect(within(studyLink).getByText(/Research study/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Continue latest study" }).getAttribute("href")).toBe("/research/studies/study-1");
    expect(screen.getByRole("link", { name: "New study" }).getAttribute("href")).toBe("/research/studies/new");
  });

  it("gives a first step when there are no studies", () => {
    query.mockImplementation((endpoint: string) => {
      if (endpoint.includes("asset-lists")) return { result: ok({ as_of: "", count: 0, lists: [] }), loading: false, refetch: vi.fn() };
      if (endpoint.endsWith("/strategies/drafts")) return { result: ok({ as_of: "", count: 0 }), loading: false, refetch: vi.fn() };
      if (endpoint.endsWith("/strategies")) return { result: ok({ as_of: "", count: 0 }), loading: false, refetch: vi.fn() };
      return { result: ok({ as_of: "", count: 0, studies: [] }), loading: false, refetch: vi.fn() };
    });

    render(<StudiesView />);

    expect(screen.getByRole("heading", { name: "Start with a research question" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Create your first study" }).getAttribute("href")).toBe("/research/studies/new");
    expect(screen.getByRole("link", { name: "Build an asset list" }).getAttribute("href")).toBe("/research/assets");
  });
  it("keeps studies beyond the recent five accessible", () => {
    query.mockImplementation((endpoint: string) => ({ result: ok(endpoint.endsWith("/studies") ? {
      count: 6, studies: Array.from({ length: 6 }, (_, index) => ({ study_id: `s${index}`, name: `Study ${index}`, kind: "substantive", created_at: "2026-10-01T00:00:00Z" })),
    } : { count: 0, lists: [] }), loading: false, refetch: vi.fn() }));
    render(<StudiesView />);
    expect(screen.queryByRole("link", { name: /Study 5/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show all 6 studies" }));
    expect(screen.getByRole("link", { name: /Study 5/ }).getAttribute("href")).toBe("/research/studies/s5");
  });

});
