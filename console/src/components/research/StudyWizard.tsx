"use client";

import { useState } from "react";
import { createRevision, createStudy } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import type { AssetList, Revision, StrategyVersion, Study, StudySettings } from "@/lib/research/types";

type VersionsBody = { versions: StrategyVersion[] };
type ListsBody = { lists: AssetList[] };
type StudyBody = { study: Study };

export type WizardState = {
  name: string;
  versionIds: string[];
  assetsText: string;
  assetListId: string;
  rangeStart: string;
  rangeEnd: string;
  development: { start: string; end: string };
  validation: { start: string; end: string };
  finalTest: { start: string; end: string };
  initialCapital: string;
  quantityPolicy: "fractional" | "whole_shares";
  slippageBps: string;
  commission: string;
  objective: "" | "return_first" | "risk_first";
  constraintValue: string;
  note: string;
  kind: "substantive" | "smoke";
};

/**
 * The form's starting state. `return_first` is the default objective selection (proposal
 * I.4); its risk-cap value, the costs and the study name start EMPTY and are required
 * inputs, never product defaults.
 */
export const EMPTY_WIZARD: WizardState = {
  name: "",
  versionIds: [],
  assetsText: "",
  assetListId: "",
  rangeStart: "",
  rangeEnd: "",
  development: { start: "", end: "" },
  validation: { start: "", end: "" },
  finalTest: { start: "", end: "" },
  initialCapital: "100000",
  quantityPolicy: "fractional",
  slippageBps: "",
  commission: "",
  objective: "return_first",
  constraintValue: "",
  note: "",
  kind: "substantive",
};

/**
 * Pure mapping from the form state to the study settings snapshot the API stores. Costs,
 * objective and constraint are sent only when entered (never pre-filled): a missing value
 * is reported by readiness as `costs_missing` / `objective_missing` / `constraint_missing`,
 * not defaulted here. Version ids are sent once each, in selection order; the immutable
 * revision keeps them as `strategy_version_ids`.
 */
export function wizardToSettings(state: WizardState): Record<string, unknown> {
  const assets = state.assetsText
    .split(/[\s,]+/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  const settings: Record<string, unknown> = {
    mode: "single_asset_independent",
    strategy_version_ids: Array.from(new Set(state.versionIds)),
    assets,
    range: { start: state.rangeStart, end: state.rangeEnd },
    windows: { development: state.development, validation: state.validation, final_test: state.finalTest },
    initial_capital: state.initialCapital,
    quantity_policy: state.quantityPolicy,
    note: state.note,
  };
  if (state.assetListId) {
    settings.asset_list_id = state.assetListId;
  }
  if (state.slippageBps.trim() !== "" && state.commission.trim() !== "") {
    settings.costs = { slippage_bps: state.slippageBps.trim(), commission_per_order: state.commission.trim() };
  }
  if (state.objective) {
    settings.objective = state.objective;
  }
  if (state.constraintValue.trim() !== "") {
    settings.constraint_value = state.constraintValue.trim();
  }
  return settings;
}

/**
 * Pure inverse for "new revision (new settings)": the latest revision's immutable
 * settings pre-fill the form so the user changes only what they mean to change. Costs,
 * objective and constraint are copied exactly as stored (empty stays empty).
 */
export function settingsToWizard(settings: StudySettings, name: string): WizardState {
  return {
    name,
    versionIds: [...settings.strategy_version_ids],
    assetsText: settings.assets.join(", "),
    assetListId: settings.asset_list_id ?? "",
    rangeStart: settings.range.start,
    rangeEnd: settings.range.end,
    development: { ...settings.windows.development },
    validation: { ...settings.windows.validation },
    finalTest: { ...settings.windows.final_test },
    initialCapital: settings.initial_capital,
    quantityPolicy: settings.quantity_policy === "whole_shares" ? "whole_shares" : "fractional",
    slippageBps: settings.costs?.slippage_bps ?? "",
    commission: settings.costs?.commission_per_order ?? "",
    objective: settings.objective === "risk_first" || settings.objective === "return_first" ? settings.objective : "",
    constraintValue: settings.constraint_value ?? "",
    note: settings.note ?? "",
    kind: "substantive",
  };
}

export type VersionFamily = { strategy_id: string; name: string; versions: StrategyVersion[] };

/** Group approved versions by family (latest first inside a family), families by latest name. */
export function groupVersions(versions: StrategyVersion[]): VersionFamily[] {
  const byFamily = new Map<string, StrategyVersion[]>();
  for (const version of versions) {
    const list = byFamily.get(version.strategy_id) ?? [];
    list.push(version);
    byFamily.set(version.strategy_id, list);
  }
  const families = Array.from(byFamily.entries()).map(([strategy_id, list]) => {
    const sorted = [...list].sort((a, b) => b.version_no - a.version_no);
    return { strategy_id, name: sorted[0].name, versions: sorted };
  });
  return families.sort((a, b) => a.name.localeCompare(b.name) || a.strategy_id.localeCompare(b.strategy_id));
}

const FIELD = "mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100";

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block text-xs text-zinc-400">
      {label}
      <input type="date" value={value} onChange={(event) => onChange(event.target.value)} className={FIELD} />
    </label>
  );
}

function VersionPicker({ selected, onToggle }: { selected: string[]; onToggle: (versionId: string) => void }) {
  const versions = useApiQuery<VersionsBody>("/api/v1/research/strategies/versions");
  if (!versions.result) {
    return <p className="mt-2 text-sm text-zinc-500">Loading…</p>;
  }
  if (!versions.result.ok) {
    return (
      <div className="mt-2">
        <ErrorState failure={versions.result} />
      </div>
    );
  }
  const families = groupVersions(versions.result.data.versions);
  if (families.length === 0) {
    return <p className="mt-2 text-sm text-zinc-500">No approved versions yet. Approve a draft under Strategies first.</p>;
  }
  const chosenPerFamily = families.map((family) => ({ family, count: family.versions.filter((v) => selected.includes(v.version_id)).length }));
  const multiFamily = chosenPerFamily.filter((entry) => entry.count > 1);
  return (
    <>
      <p className="mt-1 text-xs text-zinc-500">
        Every approved version is selectable, not only the latest; several versions of one family compare as separate candidates. Each is identified by name, version number, approval date and specification hash.
      </p>
      <ul className="mt-2 space-y-3 text-sm">
        {families.map((family) => (
          <li key={family.strategy_id}>
            <p className="text-xs font-semibold uppercase text-zinc-400">
              {family.name} <span className="font-mono normal-case text-zinc-600">family {family.strategy_id.slice(0, 8)}</span> · {family.versions.length} version(s)
            </p>
            <ul className="mt-1 space-y-1">
              {family.versions.map((version, index) => (
                <li key={version.version_id}>
                  <label className="flex flex-wrap items-center gap-2">
                    <input type="checkbox" checked={selected.includes(version.version_id)} onChange={() => onToggle(version.version_id)} />
                    <span className="text-zinc-100">
                      {version.name} v{version.version_no}
                    </span>
                    {index === 0 ? <span className="rounded border border-zinc-700 px-1 text-xs text-zinc-400">latest</span> : <span className="rounded border border-zinc-800 px-1 text-xs text-zinc-500">older</span>}
                    <span className="text-xs text-zinc-500">
                      approved {version.approved_at?.slice(0, 10) ?? "—"} · {version.source.replace(/_/g, " ")} · history {version.history_required} sessions · hash{" "}
                      <span className="font-mono">{version.spec_sha256.slice(0, 12)}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      {multiFamily.length > 0 ? (
        <p className="mt-2 text-xs text-amber-200">
          {multiFamily.map((entry) => `${entry.count} versions of ${entry.family.name}`).join("; ")} selected: they run as separate candidates and are compared side by side.
        </p>
      ) : null}
    </>
  );
}

/**
 * Study wizard. With `fromStudyId` it prepares a NEW REVISION of that study (settings
 * pre-filled from the latest revision; the revision is immutable once created); without it
 * a new study with revision 1. Nothing runs on creation: readiness lists every problem.
 */
export function StudyWizard({ onNavigate, fromStudyId = null }: { onNavigate: (href: string) => void; fromStudyId?: string | null }) {
  const lists = useApiQuery<ListsBody>("/api/v1/research/asset-lists");
  const source = useApiQuery<StudyBody>(fromStudyId ? `/api/v1/research/studies/${encodeURIComponent(fromStudyId)}` : "/api/v1/research/studies?wizard=new");
  const capability = useMutationCapability();
  const [state, setState] = useState<WizardState>(EMPTY_WIZARD);
  const [prefilledFrom, setPrefilledFrom] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Pre-fill once from the latest revision of the source study; later refetches never
  // overwrite what the user has typed.
  if (fromStudyId && source.result?.ok && prefilledFrom !== fromStudyId) {
    const revisions: Revision[] = source.result.data.study.revisions ?? [];
    const latest = revisions[revisions.length - 1];
    setPrefilledFrom(fromStudyId);
    if (latest) {
      setState(settingsToWizard(latest.settings, source.result.data.study.name));
    }
  }

  function patch(update: Partial<WizardState>) {
    setState((current) => ({ ...current, ...update }));
  }

  function toggleVersion(versionId: string) {
    setState((current) => ({
      ...current,
      versionIds: current.versionIds.includes(versionId) ? current.versionIds.filter((id) => id !== versionId) : [...current.versionIds, versionId],
    }));
  }

  async function submit() {
    setBusy(true);
    setMessage(null);
    const settings = wizardToSettings(state);
    if (fromStudyId) {
      const result = await createRevision(fromStudyId, settings);
      setBusy(false);
      if (!result.ok) {
        setMessage(result.message);
        return;
      }
      const revision = (result.data as { revision: Revision }).revision;
      onNavigate(`/research/studies/${fromStudyId}?revision=${revision.revision_id}`);
      return;
    }
    const result = await createStudy({ name: state.name, kind: state.kind, settings });
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    onNavigate(`/research/studies/${(result.data as { study: Study }).study.study_id}`);
  }

  const disabled = busy || capability.state !== "enabled";
  const constraintLabel = state.objective === "risk_first" ? "CAGR floor on the validation window (required; may be negative, zero or positive)" : "Max drawdown cap as a magnitude on the validation window (required; e.g. 0.2 means -20%)";
  return (
    <div className="space-y-4">
      {fromStudyId ? (
        <p className="rounded border border-cyan-900 bg-cyan-950/30 px-3 py-2 text-xs text-cyan-100">
          New revision of study <span className="font-mono">{fromStudyId.slice(0, 8)}</span>
          {source.result?.ok ? ` (${source.result.data.study.name})` : ""}: the form is pre-filled from its latest revision. Earlier revisions and their results stay on record.
          {source.result && !source.result.ok ? " The source study could not be loaded; the form starts empty." : ""}
        </p>
      ) : null}
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-semibold text-zinc-200">1. Strategy versions (approved only)</h2>
        <VersionPicker selected={state.versionIds} onToggle={toggleVersion} />
        <p className="mt-2 text-xs text-zinc-500">Selected: {state.versionIds.length} version(s). The immutable study snapshot keeps every selected version id.</p>
      </section>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-semibold text-zinc-200">2. Assets</h2>
        <label className="mt-2 block text-xs text-zinc-400">
          Tickers (comma or space separated)
          <input value={state.assetsText} onChange={(event) => patch({ assetsText: event.target.value })} className={FIELD} placeholder="AAPL, MSFT" />
        </label>
        <label className="mt-2 block text-xs text-zinc-400">
          Or a saved list (its tickers are snapshotted into the revision)
          <select value={state.assetListId} onChange={(event) => patch({ assetListId: event.target.value })} className={FIELD}>
            <option value="">none</option>
            {lists.result?.ok ? lists.result.data.lists.map((list) => <option key={list.list_id} value={list.list_id}>{list.name} ({list.tickers.length})</option>) : null}
          </select>
        </label>
        <p className="mt-1 text-xs text-zinc-500">The per-study limit is checked by readiness; nothing is dropped silently.</p>
      </section>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-semibold text-zinc-200">3. Dates and windows</h2>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
          <DateField label="Range start" value={state.rangeStart} onChange={(v) => patch({ rangeStart: v })} />
          <DateField label="Range end" value={state.rangeEnd} onChange={(v) => patch({ rangeEnd: v })} />
          <DateField label="Development start" value={state.development.start} onChange={(v) => patch({ development: { ...state.development, start: v } })} />
          <DateField label="Development end" value={state.development.end} onChange={(v) => patch({ development: { ...state.development, end: v } })} />
          <DateField label="Validation start" value={state.validation.start} onChange={(v) => patch({ validation: { ...state.validation, start: v } })} />
          <DateField label="Validation end" value={state.validation.end} onChange={(v) => patch({ validation: { ...state.validation, end: v } })} />
          <DateField label="Final test start" value={state.finalTest.start} onChange={(v) => patch({ finalTest: { ...state.finalTest, start: v } })} />
          <DateField label="Final test end" value={state.finalTest.end} onChange={(v) => patch({ finalTest: { ...state.finalTest, end: v } })} />
        </div>
        <p className="mt-1 text-xs text-zinc-500">The initial run covers development and validation only; the final test is a separate, freeze-gated action. Warm-up history before the development window is downloaded automatically.</p>
      </section>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-semibold text-zinc-200">4. Capital, quantities, costs, objective</h2>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          <label className="block text-xs text-zinc-400">
            Initial capital
            <input value={state.initialCapital} onChange={(event) => patch({ initialCapital: event.target.value })} className={FIELD} />
          </label>
          <label className="block text-xs text-zinc-400">
            Quantity policy
            <select value={state.quantityPolicy} onChange={(event) => patch({ quantityPolicy: event.target.value as WizardState["quantityPolicy"] })} className={FIELD}>
              <option value="fractional">fractional (research default)</option>
              <option value="whole_shares">whole shares</option>
            </select>
          </label>
          <label className="block text-xs text-zinc-400">
            Objective (default selection: return first)
            <select value={state.objective} onChange={(event) => patch({ objective: event.target.value as WizardState["objective"] })} className={FIELD}>
              <option value="return_first">return first (constraint: max drawdown cap)</option>
              <option value="risk_first">risk first (constraint: CAGR floor)</option>
            </select>
          </label>
          <label className="block text-xs text-zinc-400">
            Slippage, bps per side (required; no default — the engine&apos;s 5 bps is only an example)
            <input required value={state.slippageBps} onChange={(event) => patch({ slippageBps: event.target.value })} className={FIELD} placeholder="enter a value" />
          </label>
          <label className="block text-xs text-zinc-400">
            Commission per order (required; no default — 0 is only an example)
            <input required value={state.commission} onChange={(event) => patch({ commission: event.target.value })} className={FIELD} placeholder="enter a value" />
          </label>
          <label className="block text-xs text-zinc-400">
            {constraintLabel}
            <input required value={state.constraintValue} onChange={(event) => patch({ constraintValue: event.target.value })} className={FIELD} placeholder="enter a value (no approved default)" />
          </label>
        </div>
        <p className="mt-1 text-xs text-zinc-500">Costs and the constraint value are your inputs; a missing one makes the revision not ready (`costs_missing`, `constraint_missing`) instead of being filled in.</p>
        <label className="mt-2 block text-xs text-zinc-400">
          Study name {fromStudyId ? "(kept from the study)" : ""}
          <input value={state.name} onChange={(event) => patch({ name: event.target.value })} className={FIELD} disabled={Boolean(fromStudyId)} />
        </label>
        <label className="mt-2 block text-xs text-zinc-400">
          Note (optional)
          <input value={state.note} onChange={(event) => patch({ note: event.target.value })} className={FIELD} />
        </label>
        {!fromStudyId ? (
          <label className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
            <input type="checkbox" checked={state.kind === "smoke"} onChange={(event) => patch({ kind: event.target.checked ? "smoke" : "substantive" })} />
            Smoke run: an integration test of the pipeline, labelled as such everywhere; its settings are test configuration, not research objectives, and its outcome is not a research claim.
          </label>
        ) : null}
      </section>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={submit} disabled={disabled || (!fromStudyId && !state.name.trim())} className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950 disabled:opacity-50">
          {fromStudyId ? "Create new revision" : "Create study (revision 1)"}
        </button>
        <span className="text-xs text-zinc-500">Creating does not run anything; the readiness screen lists every problem first.</span>
        {capability.state !== "enabled" ? <span className="text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</span> : null}
      </div>
      {message ? (
        <p role="alert" className="rounded border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-100">
          {message} Your entries are kept.
        </p>
      ) : null}
    </div>
  );
}
