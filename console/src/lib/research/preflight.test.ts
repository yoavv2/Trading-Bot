import { describe, expect, it } from "vitest";
import { coverageBadge, coverageByTicker, endDateAdvice, groupPreflight, missingInputs, removableAssets } from "./preflight";
import type { StudyPreflight } from "./types";

const P: StudyPreflight = {
  calendar: { exchange: "XNYS", pinned_start: "2014-01-02", today: "2026-10-08", last_completed_session: "2026-10-07" },
  assets: [
    { ticker: "ACQQ", in_catalog: true, name: "ACQQ Inc.", catalog_start: "2026-08-14", catalog_end: "2026-10-07", earliest_development_start: null, feasibility: "no_feasible_period" },
    { ticker: "SNPE", in_catalog: true, name: null, catalog_start: "2010-01-04", catalog_end: "2026-10-07", earliest_development_start: "2010-10-20", feasibility: "cannot_cover_selected_period" },
    { ticker: "ZZZ", in_catalog: false, name: null, catalog_start: null, catalog_end: null, earliest_development_start: null, feasibility: "unknown" },
    { ticker: "NOEND", in_catalog: true, name: null, catalog_start: "2010-01-04", catalog_end: null, earliest_development_start: "2010-10-20", feasibility: "unknown" },
  ],
  warmup_sessions_max: 200,
  versions: [
    { version_id: "v1", name: "Trend", version_no: 1, history_required: 200, approved: true },
    { version_id: "v2", name: "Fast", version_no: 2, history_required: 10, approved: true },
  ],
  required_start_by_version: { v1: "2020-12-22", v2: "2021-09-24" },
  latest_common_catalog_end: "2026-10-07",
  errors: [
    { code: "coverage_start_too_late", item: "v1:ACQQ", required_start: "2020-12-22", catalog_start: "2026-08-14" },
    { code: "coverage_end_too_early", item: "v1:ACQQ", requested_end: "2026-10-08", catalog_end: "2026-10-07" },
    { code: "coverage_start_too_late", item: "v2:ACQQ", required_start: "2021-09-24", catalog_start: "2026-08-14" },
    { code: "coverage_end_too_early", item: "v2:ACQQ", requested_end: "2026-10-08", catalog_end: "2026-10-07" },
    { code: "coverage_end_too_early", item: "v1:SNPE", requested_end: "2026-10-08", catalog_end: "2026-10-07" },
    { code: "coverage_end_too_early", item: "v2:SNPE", requested_end: "2026-10-08", catalog_end: "2026-10-07" },
    { code: "asset_not_in_catalog", item: "ZZZ" },
    { code: "calendar_start_not_pinned", item: "calendar" },
  ],
  checked: { strategy_versions: 2, assets: 4, pairs: 8 },
};

describe("groupPreflight", () => {
  it("groups by asset then cause with versions listed once each, and keeps general errors apart", () => {
    const g = groupPreflight(P);
    expect(g.byAsset.map((a) => a.ticker)).toEqual(["ACQQ", "SNPE", "ZZZ"]);
    const acqq = g.byAsset[0];
    expect(acqq.causes.map((c) => c.code)).toEqual(["coverage_start_too_late", "coverage_end_too_early"]);
    expect(acqq.causes[0].summary).toBe("History starts 2026-08-14, after the warm-up 2 versions need (earliest required 2020-12-22).");
    expect(acqq.causes[0].versions).toEqual([
      { version_id: "v1", label: "Trend v1", detail: "requires history from 2020-12-22" },
      { version_id: "v2", label: "Fast v2", detail: "requires history from 2021-09-24" },
    ]);
    expect(acqq.causes[1].summary).toBe("History ends 2026-10-07, before the requested end 2026-10-08.");
    expect(g.byAsset[2].causes).toEqual([{ code: "asset_not_in_catalog", summary: "Not in the catalog.", versions: [] }]);
    expect(g.general).toEqual([{ code: "calendar_start_not_pinned", item: "calendar" }]);
    expect(g.pairErrors).toBe(6);
  });
});

describe("removableAssets", () => {
  it("lists only tickers with a confirmed failure, and keeps unanswered tickers apart as unknown", () => {
    expect(removableAssets(["ACQQ", "SNPE", "ZZZ", "NOEND", "MISSING"], P)).toEqual({ confirmed: ["ACQQ", "SNPE", "ZZZ"], unknown: ["MISSING"] });
    expect(removableAssets(["ACQQ"], null)).toEqual({ confirmed: [], unknown: ["ACQQ"] });
    expect(removableAssets([], P)).toEqual({ confirmed: [], unknown: [] });
    const g = groupPreflight(P);
    expect(g.byAsset[0].feasibility).toBe("no_feasible_period");
    expect(g.byAsset[1].earliest_development_start).toBe("2010-10-20");
  });
});

describe("coverageByTicker and badges", () => {
  it("never reports covered for a ticker the backend did not answer for", () => {
    const c = coverageByTicker(["ACQQ", "SNPE", "ZZZ", "NOEND", "MISSING"], P, null);
    expect(c.ACQQ.status).toEqual({ kind: "problem", catalog_start: "2026-08-14", catalog_end: "2026-10-07", codes: ["coverage_start_too_late", "coverage_end_too_early"] });
    expect(coverageBadge(c.ACQQ.status)).toEqual({ text: "starts 2026-08-14, ends 2026-10-07", tone: "warn" });
    expect(coverageBadge(c.SNPE.status)).toEqual({ text: "ends 2026-10-07", tone: "warn" });
    expect(c.ZZZ.status).toEqual({ kind: "not_in_catalog" });
    expect(c.NOEND.status.kind).toBe("ok"); // in catalog, no finding for it in this response
    expect(c.MISSING.status).toEqual({ kind: "unknown", reason: "not in the last check" });
    const failed = coverageByTicker(["ACQQ"], null, "boom");
    expect(failed.ACQQ.status).toEqual({ kind: "unknown", reason: "boom" });
    expect(coverageBadge(failed.ACQQ.status)).toEqual({ text: "coverage unknown", tone: "unknown" });
    expect(coverageByTicker(["ACQQ"], null, null).ACQQ.status).toEqual({ kind: "unknown", reason: "coverage not checked yet" });
  });
});

describe("endDateAdvice", () => {
  it("lists assets whose catalog ends before the end date (a missing catalog end counts) and the common end", () => {
    expect(endDateAdvice(P, "2026-10-08")).toEqual({ affected: [{ ticker: "ACQQ", catalog_end: "2026-10-07" }, { ticker: "SNPE", catalog_end: "2026-10-07" }, { ticker: "NOEND", catalog_end: null }], latestCommonEnd: "2026-10-07" });
    expect(endDateAdvice(P, "2026-10-07")).toEqual({ affected: [{ ticker: "NOEND", catalog_end: null }], latestCommonEnd: "2026-10-07" });
    expect(endDateAdvice(null, "2026-10-08")).toBeNull();
    expect(endDateAdvice(P, "")).toBeNull();
  });
});

describe("missingInputs", () => {
  it("reports empty costs and the objective-specific constraint; zero is a value", () => {
    expect(missingInputs({ slippageBps: "", commission: "", constraintValue: "", objective: "return_first" }).map((m) => [m.key, m.label])).toEqual([["costs", "Costs"], ["constraint", "Max drawdown cap"]]);
    expect(missingInputs({ slippageBps: "0", commission: "", constraintValue: "0.2", objective: "return_first" })[0].detail).toContain("commission not entered");
    expect(missingInputs({ slippageBps: "0", commission: "0", constraintValue: "", objective: "risk_first" })).toEqual([{ key: "constraint", label: "CAGR floor", detail: "The CAGR floor for risk first is not entered (readiness: constraint_missing)." }]);
    expect(missingInputs({ slippageBps: "5", commission: "1", constraintValue: "0.2", objective: "return_first" })).toEqual([]);
  });
});
