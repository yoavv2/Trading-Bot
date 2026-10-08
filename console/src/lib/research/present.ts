// Presentation helpers for the research pages: formatting and closed label maps only.
// Every verdict, status, grade, state and outcome comes from the backend contract; these
// maps turn the closed codes into operator copy and colours, and default honestly for any
// value this console version does not know yet.

import type { InputsState, ReadinessError } from "./types";

export function pct(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "n/a" : `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "n/a" : value.toFixed(digits);
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

export const INPUTS_STATE_COPY: Readonly<Record<InputsState, { label: string; tone: string; text: string }>> = {
  pending: {
    label: "Not verified",
    tone: "text-zinc-300 border-zinc-600",
    text: "The integrity check and freeze of this revision's inputs have not completed. Nothing here approves the inputs.",
  },
  failed: {
    label: "Failed",
    tone: "text-red-200 border-red-700",
    text: "The latest freeze attempt failed on integrity findings. Every backtest and evaluation of that graph was cancelled; no result exists.",
  },
  stale: {
    label: "Stale",
    tone: "text-amber-200 border-amber-700",
    text: "The inputs changed after the freeze. Results computed on the frozen inputs stay as they were; new Jobs for this revision refuse to run.",
  },
  verified: {
    label: "Verified",
    tone: "text-emerald-200 border-emerald-700",
    text: "The frozen inputs of this revision passed the integrity check and have not changed since.",
  },
};

export function inputsStateCopy(state: string): { label: string; tone: string; text: string } {
  return (INPUTS_STATE_COPY as Record<string, { label: string; tone: string; text: string }>)[state] ?? {
    label: state,
    tone: "text-zinc-300 border-zinc-600",
    text: "Unknown input state reported by the API.",
  };
}

const READINESS_CODE_COPY: Readonly<Record<string, string>> = {
  mode_not_supported_yet: "Evaluation mode not supported yet (only single-asset independent tests run in this version)",
  strategy_version_not_approved: "Strategy version is not an approved version",
  asset_limit_exceeded: "More assets than the configured per-study limit (no asset was dropped)",
  asset_not_in_catalog: "Asset is not in the catalog",
  coverage_start_too_late: "Catalog history starts after the warm-up this pair needs",
  coverage_end_too_early: "Catalog history ends before the requested range end",
  warmup_not_satisfiable: "Warm-up precedes the pinned calendar start",
  windows_overlap_or_unordered: "Windows overlap or are out of order (development < validation < final test)",
  window_outside_range: "Window lies outside the study range",
  costs_missing: "Costs were not entered (slippage per side, commission per order)",
  objective_missing: "Ranking objective was not chosen",
  constraint_missing: "Constraint value was not entered",
  calendar_start_not_pinned: "The research calendar start is not pinned on this API",
  market_sessions_not_synced: "Exchange sessions are not stored for the whole download range; submit a sync-market-sessions Job for from_date..to_date (New Job page) before running",
  integrity_error: "Integrity finding on the downloaded rows",
  inputs_changed_after_freeze: "Inputs changed after the freeze",
  freeze_failed: "The freeze Job failed",
  freeze_cancelled: "The freeze Job was cancelled",
};

export function readinessErrorCopy(error: ReadinessError): string {
  const base = READINESS_CODE_COPY[error.code] ?? error.code;
  const extras = Object.entries(error)
    .filter(([key]) => !["code", "item"].includes(key))
    .map(([key, value]) => `${key}: ${String(value)}`);
  return extras.length ? `${base} (${extras.join(", ")})` : base;
}

export const STATUS_COPY: Readonly<Record<string, string>> = {
  eligible: "eligible",
  not_eligible: "not eligible (constraint failed on validation)",
  insufficient_evidence: "insufficient evidence",
  not_evaluable: "not evaluable",
};

export const VERDICT_TONE: Readonly<Record<string, string>> = {
  leading_candidate_identified: "text-emerald-300",
  insufficient_evidence: "text-amber-300",
  no_candidate_qualifies: "text-zinc-300",
};

export const OUTCOME_TONE: Readonly<Record<string, string>> = {
  frozen_criteria_met: "text-emerald-300",
  frozen_criteria_not_met: "text-red-300",
  insufficient_evidence: "text-amber-300",
};

export const GRADE_TONE: Readonly<Record<string, string>> = {
  meets_predefined_study_conditions: "text-emerald-300",
  thin: "text-amber-300",
  insufficient: "text-red-300",
};

export function tone(map: Readonly<Record<string, string>>, key: string | null | undefined): string {
  return (key && map[key]) || "text-zinc-300";
}
