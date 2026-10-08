import { describe, expect, it } from "vitest";
import { DEFAULT_SPLIT, computeWindows, dayNumber, inclusiveDays, splitFromWindows, splitProblem, validatePeriod, windowsEqual, type Windows } from "./windows";
import { assetTags, mergeAssets, normalizeTicker, parseTickers } from "./assets";

describe("window math", () => {
  it("parses only real ISO dates", () => {
    expect(dayNumber("2024-02-29")).not.toBeNull();
    expect(dayNumber("2023-02-29")).toBeNull();
    expect(dayNumber("2024-13-01")).toBeNull();
    expect(dayNumber("")).toBeNull();
    expect(inclusiveDays("2024-01-01", "2024-01-01")).toBe(1);
    expect(inclusiveDays("2024-01-01", "2024-01-10")).toBe(10);
  });

  it("splits an inclusive range into contiguous, non-overlapping windows covering it exactly", () => {
    const windows = computeWindows("2018-01-01", "2025-12-31", DEFAULT_SPLIT)!;
    const total = inclusiveDays("2018-01-01", "2025-12-31")!; // 2922
    expect(windows.development.start).toBe("2018-01-01");
    expect(windows.final_test.end).toBe("2025-12-31");
    expect(inclusiveDays(windows.development.start, windows.development.end)).toBe(Math.round(total * 0.6));
    expect(inclusiveDays(windows.validation.start, windows.validation.end)).toBe(Math.round(total * 0.2));
    // Boundaries are consecutive days: the backend requires dev.end < val.start and val.end < ft.start.
    expect(dayNumber(windows.validation.start)).toBe(dayNumber(windows.development.end)! + 1);
    expect(dayNumber(windows.final_test.start)).toBe(dayNumber(windows.validation.end)! + 1);
    const sum = ["development", "validation", "final_test"].map((role) => inclusiveDays(windows[role as keyof Windows].start, windows[role as keyof Windows].end)!).reduce((a, b) => a + b, 0);
    expect(sum).toBe(total);
    expect(validatePeriod("2018-01-01", "2025-12-31", windows)).toEqual([]);
  });

  it("keeps one day per window on tiny ranges and refuses ranges under three days", () => {
    expect(computeWindows("2024-01-01", "2024-01-02", DEFAULT_SPLIT)).toBeNull();
    const three = computeWindows("2024-01-01", "2024-01-03", DEFAULT_SPLIT)!;
    expect(three).toEqual({
      development: { start: "2024-01-01", end: "2024-01-01" },
      validation: { start: "2024-01-02", end: "2024-01-02" },
      final_test: { start: "2024-01-03", end: "2024-01-03" },
    });
    const four = computeWindows("2024-01-01", "2024-01-04", { development_pct: 98, validation_pct: 1, final_test_pct: 1 })!;
    expect(four.final_test).toEqual({ start: "2024-01-04", end: "2024-01-04" });
    expect(validatePeriod("2024-01-01", "2024-01-04", four)).toEqual([]);
  });

  it("rejects invalid ranges and splits", () => {
    expect(computeWindows("2024-01-10", "2024-01-01", DEFAULT_SPLIT)).toBeNull();
    expect(computeWindows("", "2024-01-01", DEFAULT_SPLIT)).toBeNull();
    expect(computeWindows("2018-01-01", "2025-12-31", { development_pct: 60, validation_pct: 20, final_test_pct: 30 })).toBeNull();
    expect(splitProblem({ development_pct: 60, validation_pct: 20, final_test_pct: 30 })).toMatch(/110%/);
    expect(splitProblem({ development_pct: 100, validation_pct: 0, final_test_pct: 0 })).toMatch(/above 0%/);
    expect(splitProblem({ development_pct: 60.5, validation_pct: 19.5, final_test_pct: 20 })).toMatch(/whole number/);
    expect(splitProblem(DEFAULT_SPLIT)).toBeNull();
  });

  it("validates custom windows against the backend rules, keyed by control", () => {
    const issues = validatePeriod("2018-01-01", "2025-12-31", {
      development: { start: "2017-12-01", end: "2020-01-01" },
      validation: { start: "2020-01-01", end: "2022-01-01" },
      final_test: { start: "2021-06-01", end: "2026-01-01" },
    });
    expect(issues.map((i) => i.field).sort()).toEqual(["development.start", "final_test.end", "final_test.start", "validation.start"]);
    expect(issues.find((i) => i.field === "validation.start")!.message).toMatch(/after development ends/);
    expect(issues.find((i) => i.field === "development.start")!.message).toMatch(/before the research period/);
    const reversed = validatePeriod("2018-01-01", "2025-12-31", {
      development: { start: "2019-01-01", end: "2018-06-01" },
      validation: { start: "2020-01-01", end: "2021-01-01" },
      final_test: { start: "2022-01-01", end: "2023-01-01" },
    });
    expect(reversed).toEqual([{ field: "development.end", message: "Development ends before it starts." }]);
    const empty = validatePeriod("", "", { development: { start: "", end: "" }, validation: { start: "", end: "" }, final_test: { start: "", end: "" } });
    expect(empty.map((i) => i.field)).toContain("rangeStart");
    expect(empty.map((i) => i.field)).toContain("final_test.end");
  });

  it("derives display shares from custom windows and compares windows", () => {
    const windows = computeWindows("2020-01-01", "2020-12-31", { development_pct: 50, validation_pct: 25, final_test_pct: 25 })!;
    expect(splitFromWindows("2020-01-01", "2020-12-31", windows)).toEqual({ development_pct: 50, validation_pct: 25, final_test_pct: 25 });
    expect(windowsEqual(windows, { ...windows })).toBe(true);
    expect(windowsEqual(windows, { ...windows, final_test: { start: windows.final_test.start, end: "2020-12-30" } })).toBe(false);
  });
});

describe("asset helpers", () => {
  it("normalises like the backend and parses bulk input", () => {
    expect(normalizeTicker(" brk.b ")).toBe("BRK-B");
    expect(parseTickers("aapl, MSFT;nvda\n aapl  brk.b")).toEqual(["AAPL", "MSFT", "NVDA", "BRK-B"]);
  });

  it("builds the exact backend snapshot: manual first, then the list, de-duplicated", () => {
    expect(mergeAssets(["msft", "AAPL"], ["AAPL", "NVDA", "msft"])).toEqual(["MSFT", "AAPL", "NVDA"]);
    expect(assetTags(["msft"], ["AAPL", "MSFT"])).toEqual([
      { ticker: "MSFT", source: "manual" },
      { ticker: "AAPL", source: "list" },
    ]);
  });
});
