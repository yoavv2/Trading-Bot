import { describe, expect, it } from "vitest";
import { filterByRun, isCapped, OPERATIONS_MAX_LIMIT } from "./runScopedFilter";

type FakeItem = { run_id: string; label: string };

describe("filterByRun", () => {
  it("returns only items whose run_id matches", () => {
    const items: FakeItem[] = [
      { run_id: "run-a", label: "1" },
      { run_id: "run-b", label: "2" },
      { run_id: "run-a", label: "3" },
    ];

    expect(filterByRun(items, "run-a")).toEqual([
      { run_id: "run-a", label: "1" },
      { run_id: "run-a", label: "3" },
    ]);
  });

  it("returns [] on an empty array", () => {
    expect(filterByRun([], "run-a")).toEqual([]);
  });

  it("returns [] when nothing matches", () => {
    const items: FakeItem[] = [
      { run_id: "run-x", label: "1" },
      { run_id: "run-y", label: "2" },
    ];

    expect(filterByRun(items, "run-a")).toEqual([]);
  });
});

describe("isCapped", () => {
  it("is false below the API max", () => {
    expect(isCapped(99)).toBe(false);
    expect(isCapped(12)).toBe(false);
    expect(isCapped(0)).toBe(false);
  });

  it("is true at and above the API max", () => {
    expect(isCapped(100)).toBe(true);
    expect(isCapped(101)).toBe(true);
  });

  it("exposes the OPERATIONS_MAX_LIMIT constant tied to the backend cap", () => {
    expect(OPERATIONS_MAX_LIMIT).toBe(100);
    expect(isCapped(OPERATIONS_MAX_LIMIT)).toBe(true);
  });
});

describe("combined filter + cap detection", () => {
  it("empty-and-capped: 100 raw items where NONE match yields [] but isCapped is true", () => {
    const items: FakeItem[] = Array.from({ length: 100 }, (_, i) => ({
      run_id: "some-other-run",
      label: String(i),
    }));

    const matched = filterByRun(items, "run-a");
    expect(matched).toEqual([]);
    expect(isCapped(items.length)).toBe(true);
  });

  it("12 raw items, 3 match: filterByRun returns the 3, isCapped is false", () => {
    const items: FakeItem[] = [
      { run_id: "run-a", label: "1" },
      { run_id: "run-b", label: "2" },
      { run_id: "run-a", label: "3" },
      { run_id: "run-c", label: "4" },
      { run_id: "run-b", label: "5" },
      { run_id: "run-a", label: "6" },
      { run_id: "run-d", label: "7" },
      { run_id: "run-b", label: "8" },
      { run_id: "run-c", label: "9" },
      { run_id: "run-d", label: "10" },
      { run_id: "run-e", label: "11" },
      { run_id: "run-f", label: "12" },
    ];

    const matched = filterByRun(items, "run-a");
    expect(matched).toEqual([
      { run_id: "run-a", label: "1" },
      { run_id: "run-a", label: "3" },
      { run_id: "run-a", label: "6" },
    ]);
    expect(isCapped(items.length)).toBe(false);
  });
});
