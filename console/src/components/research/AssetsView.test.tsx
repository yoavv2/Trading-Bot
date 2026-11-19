// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AssetsView } from "./AssetsView";

const query = vi.hoisted(() => vi.fn());
const createList = vi.hoisted(() => vi.fn());
const updateList = vi.hoisted(() => vi.fn());
const deleteList = vi.hoisted(() => vi.fn());

vi.mock("@/lib/useApiQuery", () => ({ useApiQuery: query }));
vi.mock("@/lib/useMutationCapability", () => ({ useMutationCapability: () => ({ state: "enabled", reason: null, loading: false, catalog: null }) }));
vi.mock("@/lib/api", () => ({ createAssetList: createList, updateAssetList: updateList, deleteAssetList: deleteList }));

afterEach(() => {
  cleanup();
  query.mockReset();
  createList.mockReset();
  updateList.mockReset();
  deleteList.mockReset();
});

function ok<T>(data: T) {
  return { ok: true as const, data, asOf: new Date("2026-10-08T12:00:00Z") };
}

function setup() {
  const refetchLists = vi.fn();
  query.mockImplementation((endpoint: string) => {
    if (endpoint.includes("catalog/search")) {
      return { result: ok({
        query: "AAPL", count: 1,
        items: [{ ticker: "AAPL", name: "Apple Inc.", name_source: "provider", exchange: "NASDAQ", asset_type: "stock", currency: "USD", catalog_start: "2010-01-01", catalog_end: "2026-10-07" }],
        name_coverage: { rows_named: 8, rows_total: 10 }, name_search_note: "Name search only matches catalog rows with names.",
      }), loading: false, refetch: vi.fn() };
    }
    if (endpoint.endsWith("/catalog")) {
      return { result: ok({ as_of: "", catalog: { rows_total: 10, rows_named: 8, name_search_note: "Name search only matches catalog rows with names.", coverage_note: "Readiness checks actual history for each asset and strategy pair.", last_synced_at: "2026-10-07", max_assets_per_study: 20 } }), loading: false, refetch: vi.fn() };
    }
    return { result: ok({ as_of: "", count: 1, lists: [{ list_id: "list-1", name: "Core", tickers: ["MSFT"], updated_at: null }] }), loading: false, refetch: refetchLists };
  });
  return { refetchLists };
}

describe("AssetsView", () => {
  it("searches the catalog and saves selected tickers as a list", async () => {
    const { refetchLists } = setup();
    createList.mockResolvedValue({ ok: true, data: { list: { list_id: "list-2" } } });
    render(<AssetsView />);

    expect(screen.getByText("8 of 10 rows have a company name.")).toBeTruthy();
    expect(screen.getByText(/each asset and strategy pair still needs a readiness check/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Ticker or company name"), { target: { value: "AAPL" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const row = screen.getByRole("row", { name: /AAPL Apple Inc/ });
    fireEvent.click(within(row).getByRole("checkbox", { name: "Select AAPL" }));
    fireEvent.change(screen.getByLabelText("List name"), { target: { value: "Watchlist" } });
    fireEvent.click(screen.getByRole("button", { name: "Save list" }));

    await waitFor(() => expect(createList).toHaveBeenCalledWith({ name: "Watchlist", tickers: ["AAPL"] }));
    expect(refetchLists).toHaveBeenCalledOnce();
  });

  it("loads an existing list into the editor and supports update and delete", async () => {
    setup();
    updateList.mockResolvedValue({ ok: true, data: { list: { list_id: "list-1" } } });
    deleteList.mockResolvedValue({ ok: true, data: {} });
    render(<AssetsView />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect((screen.getByLabelText("List name") as HTMLInputElement).value).toBe("Core");
    fireEvent.change(screen.getByLabelText("List name"), { target: { value: "Core assets" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(updateList).toHaveBeenCalledWith("list-1", { name: "Core assets", tickers: ["MSFT"] }));

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteList).toHaveBeenCalledWith("list-1"));
  });
});
