// Study preflight (POST /api/v1/research/studies/preflight): the backend's readiness
// coverage checks run against the wizard's current entries, without creating a study.
// These helpers turn the response into per-asset coverage statuses for the tags, grouped
// findings (asset → cause → affected versions) and the end-date advice. Anything the
// backend did not answer for is UNKNOWN, never assumed covered.

import type { ReadinessError, StudyPreflight } from "./types";
import type { WizardState } from "@/components/research/StudyWizard";

export type CoverageStatus =
  | { kind: "unknown"; reason: string }
  | { kind: "not_in_catalog" }
  | { kind: "ok"; catalog_start: string | null; catalog_end: string | null }
  | { kind: "problem"; catalog_start: string | null; catalog_end: string | null; codes: string[] };

export type AssetCoverage = { ticker: string; status: CoverageStatus; name: string | null };

export type CauseGroup = { code: string; summary: string; versions: { version_id: string; label: string; detail: string }[] };
export type AssetGroup = { ticker: string; name: string | null; catalog_start: string | null; catalog_end: string | null; causes: CauseGroup[] };
export type Grouped = { byAsset: AssetGroup[]; general: ReadinessError[]; pairErrors: number };

const CAUSE_SUMMARY: Record<string, (first: ReadinessError, count: number) => string> = {
  coverage_start_too_late: (e, n) => `History starts ${String(e.catalog_start ?? "—")}, after the warm-up ${n > 1 ? `${n} versions need` : "this version needs"} (earliest required ${String(e.required_start ?? "—")}).`,
  coverage_end_too_early: (e) => `History ends ${String(e.catalog_end ?? "—")}, before the requested end ${String(e.requested_end ?? "—")}.`,
  warmup_not_satisfiable: () => "Warm-up would start before the pinned calendar start.",
  asset_not_in_catalog: () => "Not in the catalog.",
};

const PAIR_CODES = new Set(Object.keys(CAUSE_SUMMARY));

function versionLabel(preflight: StudyPreflight, versionId: string): string {
  const v = preflight.versions.find((entry) => entry.version_id === versionId);
  return v && v.name ? `${v.name} v${v.version_no}` : versionId.slice(0, 8);
}

function pairDetail(error: ReadinessError): string {
  if (error.code === "coverage_start_too_late") {
    return `requires history from ${String(error.required_start ?? "—")}`;
  }
  if (error.code === "warmup_not_satisfiable") {
    return `${String(error.history_required ?? "?")} sessions of warm-up`;
  }
  return "";
}

/** Group the preflight errors by asset, then by cause, with the affected versions listed once each. */
export function groupPreflight(preflight: StudyPreflight): Grouped {
  const byAsset = new Map<string, AssetGroup>();
  const general: ReadinessError[] = [];
  let pairErrors = 0;
  const ensure = (ticker: string): AssetGroup => {
    let group = byAsset.get(ticker);
    if (!group) {
      const asset = preflight.assets.find((a) => a.ticker === ticker);
      group = { ticker, name: asset?.name ?? null, catalog_start: asset?.catalog_start ?? null, catalog_end: asset?.catalog_end ?? null, causes: [] };
      byAsset.set(ticker, group);
    }
    return group;
  };
  for (const error of preflight.errors) {
    if (error.code === "asset_not_in_catalog") {
      const group = ensure(error.item);
      if (!group.causes.some((c) => c.code === error.code)) {
        group.causes.push({ code: error.code, summary: CAUSE_SUMMARY[error.code](error, 1), versions: [] });
      }
      continue;
    }
    const colon = error.item.indexOf(":");
    if (!PAIR_CODES.has(error.code) || colon < 0) {
      general.push(error);
      continue;
    }
    pairErrors += 1;
    const versionId = error.item.slice(0, colon);
    const ticker = error.item.slice(colon + 1);
    const group = ensure(ticker);
    let cause = group.causes.find((c) => c.code === error.code);
    if (!cause) {
      cause = { code: error.code, summary: "", versions: [] };
      group.causes.push(cause);
    }
    cause.versions.push({ version_id: versionId, label: versionLabel(preflight, versionId), detail: pairDetail(error) });
  }
  for (const group of byAsset.values()) {
    for (const cause of group.causes) {
      if (cause.versions.length > 0) {
        const first = preflight.errors.find((e) => e.code === cause.code && e.item.endsWith(`:${group.ticker}`))!;
        // For coverage_start_too_late the earliest required start is the binding one.
        const earliest = preflight.errors
          .filter((e) => e.code === cause.code && e.item.endsWith(`:${group.ticker}`) && typeof e.required_start === "string")
          .map((e) => e.required_start as string)
          .sort()[0];
        cause.summary = CAUSE_SUMMARY[cause.code]({ ...first, required_start: earliest ?? first.required_start }, cause.versions.length);
      }
    }
  }
  return { byAsset: Array.from(byAsset.values()), general, pairErrors };
}

/**
 * Per-ticker status for the tags. `requested` is every ticker shown; a ticker absent from
 * the response (or when the response is missing/failed) is UNKNOWN, not covered.
 */
export function coverageByTicker(requested: string[], preflight: StudyPreflight | null, failure: string | null): Record<string, AssetCoverage> {
  const out: Record<string, AssetCoverage> = {};
  const grouped = preflight ? groupPreflight(preflight) : null;
  for (const ticker of requested) {
    const asset = preflight?.assets.find((a) => a.ticker === ticker);
    if (!preflight || !asset) {
      out[ticker] = { ticker, name: null, status: { kind: "unknown", reason: failure ?? (preflight ? "not in the last check" : "coverage not checked yet") } };
      continue;
    }
    if (!asset.in_catalog) {
      out[ticker] = { ticker, name: asset.name, status: { kind: "not_in_catalog" } };
      continue;
    }
    const codes = grouped?.byAsset.find((g) => g.ticker === ticker)?.causes.map((c) => c.code) ?? [];
    out[ticker] = {
      ticker,
      name: asset.name,
      status: codes.length > 0 ? { kind: "problem", catalog_start: asset.catalog_start, catalog_end: asset.catalog_end, codes } : { kind: "ok", catalog_start: asset.catalog_start, catalog_end: asset.catalog_end },
    };
  }
  return out;
}

export function coverageBadge(status: CoverageStatus): { text: string; tone: "ok" | "warn" | "unknown" } {
  switch (status.kind) {
    case "ok":
      return { text: "covers period", tone: "ok" };
    case "not_in_catalog":
      return { text: "not in catalog", tone: "warn" };
    case "unknown":
      return { text: "coverage unknown", tone: "unknown" };
    case "problem": {
      const parts: string[] = [];
      if (status.codes.includes("coverage_start_too_late") || status.codes.includes("warmup_not_satisfiable")) {
        parts.push(`starts ${status.catalog_start ?? "—"}`);
      }
      if (status.codes.includes("coverage_end_too_early")) {
        parts.push(`ends ${status.catalog_end ?? "—"}`);
      }
      return { text: parts.join(", ") || "coverage problem", tone: "warn" };
    }
  }
}

export type EndDateAdvice = { affected: { ticker: string; catalog_end: string | null }[]; latestCommonEnd: string | null } | null;

/** Assets whose catalog ends before the requested end, with the explicit alternative end. */
export function endDateAdvice(preflight: StudyPreflight | null, rangeEnd: string): EndDateAdvice {
  if (!preflight || !rangeEnd) {
    return null;
  }
  const affected = preflight.assets.filter((a) => a.in_catalog && (a.catalog_end === null || a.catalog_end < rangeEnd)).map((a) => ({ ticker: a.ticker, catalog_end: a.catalog_end }));
  if (affected.length === 0) {
    return null;
  }
  return { affected, latestCommonEnd: preflight.latest_common_catalog_end };
}

export type MissingInput = { key: "costs" | "constraint"; label: string; detail: string };

/**
 * The readiness items the wizard can predict from the form alone (costs_missing,
 * constraint_missing). Zero is a value: "0" counts as entered. Objective is always chosen
 * in the form, so objective_missing cannot occur here.
 */
export function missingInputs(state: Pick<WizardState, "slippageBps" | "commission" | "constraintValue" | "objective">): MissingInput[] {
  const out: MissingInput[] = [];
  if (state.slippageBps.trim() === "" || state.commission.trim() === "") {
    const which = [state.slippageBps.trim() === "" ? "slippage" : null, state.commission.trim() === "" ? "commission" : null].filter(Boolean).join(" and ");
    out.push({ key: "costs", label: "Costs", detail: `${which} not entered (readiness: costs_missing). Zero is accepted when you mean it.` });
  }
  if (state.constraintValue.trim() === "") {
    out.push({
      key: "constraint",
      label: state.objective === "risk_first" ? "CAGR floor" : "Max drawdown cap",
      detail: `${state.objective === "risk_first" ? "The CAGR floor" : "The max drawdown cap"} for ${state.objective === "risk_first" ? "risk first" : "return first"} is not entered (readiness: constraint_missing).`,
    });
  }
  return out;
}
