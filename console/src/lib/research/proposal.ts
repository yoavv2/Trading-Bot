// Pure mapping from a validated study proposal to the wizard form. "Apply to form" only
// patches the fields the proposal actually filled in (empty = not proposed); everything
// else keeps its current value. The change list names each field that will be replaced
// with its current and proposed values so the user can review before applying.

import { wizardWindows, type WizardState } from "@/components/research/StudyWizard";
import type { StudyProposal } from "@/lib/research/types";
import { emptyWindows } from "@/components/research/ResearchPeriod";
import { WINDOW_LABELS, computeWindows, type Split, type Window, type WindowRole, type Windows } from "./windows";

export type FieldChange = { key: string; label: string; from: string; to: string };

export type Application = {
  patch: Partial<WizardState>;
  changes: FieldChange[];
  /** The research period as it will stand after applying: the one source for the preview. */
  period: { rangeStart: string; rangeEnd: string; mode: WizardState["periodMode"]; split: Split; windows: Windows };
};

function show(value: string | string[] | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? "—" : value.join(", ");
  }
  return value.trim() === "" ? "—" : value;
}

function span(w: Window): string {
  return `${show(w.start)} → ${show(w.end)}`;
}

const WINDOW_KEYS: { key: "development" | "validation" | "finalTest"; role: WindowRole }[] = [
  { key: "development", role: "development" },
  { key: "validation", role: "validation" },
  { key: "finalTest", role: "final_test" },
];

/**
 * Plan what applying `proposal` does to `current`. `isRevision` keeps the study name and
 * kind (both belong to the study, not the revision). Strategies are applied only through
 * ids the backend resolved against approved versions; unresolved references are never
 * guessed.
 *
 * Periods, one calculation for preview, change table and applied state:
 * - explicit windows → custom mode with those dates;
 * - a split, or a range boundary that actually differs from the form → preset mode with
 *   that split (else the form's split), windows recomputed; the split is patched together
 *   with the windows so the controls, the timeline and the submitted dates agree;
 * - otherwise (no windows, no split, range absent or unchanged) the periods are left
 *   exactly as they are, custom periods included.
 * Every window whose dates change is listed individually.
 */
export function planApplication(proposal: StudyProposal, current: WizardState, isRevision: boolean): Application {
  const s = proposal.settings;
  const patch: Partial<WizardState> = {};
  const changes: FieldChange[] = [];

  function change<K extends keyof WizardState>(key: K, label: string, from: string, to: string, value: WizardState[K]) {
    if (from === to) {
      return; // proposing the value the form already holds is not a change
    }
    patch[key] = value;
    changes.push({ key, label, from, to });
  }

  if (!isRevision && s.name.trim()) {
    change("name", "Study name", show(current.name), s.name.trim(), s.name.trim());
  }
  const versionIds = Array.from(new Set(s.strategies.resolved.map((entry) => entry.version_id)));
  if (versionIds.length > 0) {
    change(
      "versionIds",
      "Strategy versions",
      current.versionIds.length === 0 ? "—" : `${current.versionIds.length} selected`,
      s.strategies.resolved.map((entry) => `${entry.name} v${entry.version_no}`).join(", "),
      versionIds,
    );
  }
  const assets = s.assets.map((asset) => asset.ticker);
  if (assets.length > 0) {
    change("assets", "Assets", show(current.assets), assets.join(", "), assets);
    if (current.assetListId) {
      change("assetListId", "Saved list", "selected", "none (the proposal names its own tickers)", "");
    }
  }

  // -- research period (single calculation) --------------------------------------------
  const rangeStart = s.range_start.trim() || current.rangeStart;
  const rangeEnd = s.range_end.trim() || current.rangeEnd;
  const rangeChanged = rangeStart !== current.rangeStart || rangeEnd !== current.rangeEnd;
  if (rangeStart !== current.rangeStart) {
    change("rangeStart", "Start date", show(current.rangeStart), rangeStart, rangeStart);
  }
  if (rangeEnd !== current.rangeEnd) {
    change("rangeEnd", "End date", show(current.rangeEnd), rangeEnd, rangeEnd);
  }
  const currentWindows = wizardWindows(current);
  let period: Application["period"] = { rangeStart, rangeEnd, mode: current.periodMode, split: current.split, windows: currentWindows };
  if (s.windows) {
    period = { rangeStart, rangeEnd, mode: "custom", split: current.split, windows: { development: { ...s.windows.development }, validation: { ...s.windows.validation }, final_test: { ...s.windows.final_test } } };
    if (current.periodMode !== "custom") {
      changes.push({ key: "periodMode", label: "Periods", from: "preset split", to: "custom (dates as proposed)" });
    }
    patch.periodMode = "custom";
  } else if (s.split || rangeChanged) {
    const split: Split = s.split ? { ...s.split } : { ...current.split };
    const windows = computeWindows(rangeStart, rangeEnd, split) ?? emptyWindows();
    period = { rangeStart, rangeEnd, mode: "preset", split, windows };
    if (s.split && (split.development_pct !== current.split.development_pct || split.validation_pct !== current.split.validation_pct || split.final_test_pct !== current.split.final_test_pct)) {
      changes.push({ key: "split", label: "Split", from: `${current.split.development_pct} / ${current.split.validation_pct} / ${current.split.final_test_pct}`, to: `${split.development_pct} / ${split.validation_pct} / ${split.final_test_pct}` });
    }
    if (current.periodMode !== "preset") {
      changes.push({ key: "periodMode", label: "Periods", from: "custom", to: `preset split ${split.development_pct} / ${split.validation_pct} / ${split.final_test_pct}` });
    }
    patch.periodMode = "preset";
    patch.split = split;
  }
  for (const { key, role } of WINDOW_KEYS) {
    const before = currentWindows[role];
    const after = period.windows[role];
    if (before.start !== after.start || before.end !== after.end) {
      change(key, WINDOW_LABELS[role], span(before), span(after), { ...after });
    } else if (period.mode !== current.periodMode || patch.split) {
      patch[key] = { ...after };
    }
  }

  if (s.initial_capital.trim()) {
    change("initialCapital", "Initial capital", show(current.initialCapital), s.initial_capital.trim(), s.initial_capital.trim());
  }
  if (s.quantity_policy) {
    change("quantityPolicy", "Quantity policy", current.quantityPolicy, s.quantity_policy, s.quantity_policy);
  }
  if (s.slippage_bps.trim()) {
    change("slippageBps", "Slippage (bps)", show(current.slippageBps), s.slippage_bps.trim(), s.slippage_bps.trim());
  }
  if (s.commission_per_order.trim()) {
    change("commission", "Commission per order", show(current.commission), s.commission_per_order.trim(), s.commission_per_order.trim());
  }
  if (s.objective) {
    change("objective", "Objective", show(current.objective), s.objective, s.objective);
  }
  if (s.constraint_value.trim()) {
    change("constraintValue", "Constraint value", show(current.constraintValue), s.constraint_value.trim(), s.constraint_value.trim());
  }
  if (s.note.trim()) {
    change("note", "Note", show(current.note), s.note.trim(), s.note.trim());
  }
  if (!isRevision && s.kind) {
    change("kind", "Kind", current.kind, s.kind, s.kind);
  }
  return { patch, changes, period };
}
