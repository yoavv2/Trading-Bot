"use client";

import { useState } from "react";
import { createRevision, createStudy } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import type { AssetList, CatalogCoverage, Revision, StrategyVersion, Study, StudySettings } from "@/lib/research/types";
import { dedupe, mergeAssets, normalizeTicker } from "@/lib/research/assets";
import { coverageByTicker, endDateAdvice, groupPreflight, missingInputs, removableAssets } from "@/lib/research/preflight";
import { useStudyPreflight } from "@/lib/useStudyPreflight";
import { DEFAULT_SPLIT, computeWindows, splitFromWindows, validatePeriod, windowsEqual, type Split, type Windows } from "@/lib/research/windows";
import { AssetPicker } from "./AssetPicker";
import { ResearchPeriod, type PeriodMode } from "./ResearchPeriod";
import { StudyAssistant } from "./StudyAssistant";

type VersionsBody = { versions: StrategyVersion[] };
type ListsBody = { lists: AssetList[] };
type StudyBody = { study: Study };
type CoverageBody = { catalog: CatalogCoverage };

export type WizardState = {
  name: string;
  versionIds: string[];
  /** Manually chosen tickers (normalised). A saved list's tickers are added by the backend. */
  assets: string[];
  assetListId: string;
  rangeStart: string;
  rangeEnd: string;
  /** The split preset; in `preset` mode the windows are always derived from it. */
  split: Split;
  periodMode: PeriodMode;
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
 * inputs, never product defaults. The 60/20/20 split is a convenience preset for the
 * window boundaries, not a research methodology.
 */
export const EMPTY_WIZARD: WizardState = {
  name: "",
  versionIds: [],
  assets: [],
  assetListId: "",
  rangeStart: "",
  rangeEnd: "",
  split: { ...DEFAULT_SPLIT },
  periodMode: "preset",
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

export function wizardWindows(state: WizardState): Windows {
  return { development: state.development, validation: state.validation, final_test: state.finalTest };
}

/**
 * Pure mapping from the form state to the study settings snapshot the API stores. Costs,
 * objective and constraint are sent only when entered (never pre-filled): a missing value
 * is reported by readiness as `costs_missing` / `objective_missing` / `constraint_missing`,
 * not defaulted here. Version ids are sent once each, in selection order; the immutable
 * revision keeps them as `strategy_version_ids`. Manual tickers are sent normalised and
 * de-duplicated; with `asset_list_id` the backend appends the list's tickers (the tags on
 * screen show that same union).
 */
export function wizardToSettings(state: WizardState): Record<string, unknown> {
  const settings: Record<string, unknown> = {
    mode: "single_asset_independent",
    strategy_version_ids: Array.from(new Set(state.versionIds)),
    assets: dedupe(state.assets.map(normalizeTicker)),
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
 * objective and constraint are copied exactly as stored (empty stays empty). The stored
 * windows are kept EXACTLY: the form opens in custom mode unless the default preset
 * reproduces them to the day. The stored asset snapshot becomes manual tags (the list,
 * if any, stays referenced; tickers it gained since are shown as list tags).
 */
export function settingsToWizard(settings: StudySettings, name: string): WizardState {
  const windows: Windows = {
    development: { ...settings.windows.development },
    validation: { ...settings.windows.validation },
    final_test: { ...settings.windows.final_test },
  };
  const preset = computeWindows(settings.range.start, settings.range.end, DEFAULT_SPLIT);
  const matchesPreset = preset !== null && windowsEqual(preset, windows);
  const split = matchesPreset ? { ...DEFAULT_SPLIT } : (splitFromWindows(settings.range.start, settings.range.end, windows) ?? { ...DEFAULT_SPLIT });
  return {
    name,
    versionIds: [...settings.strategy_version_ids],
    assets: [...settings.assets],
    assetListId: settings.asset_list_id ?? "",
    rangeStart: settings.range.start,
    rangeEnd: settings.range.end,
    split,
    periodMode: matchesPreset ? "preset" : "custom",
    development: windows.development,
    validation: windows.validation,
    finalTest: windows.final_test,
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

/** Everything that stops creation before the API would refuse it (pydantic 422), by control. */
export function wizardBlockers(state: WizardState, isRevision: boolean, listTickers: string[]): string[] {
  const blockers: string[] = [];
  if (!isRevision && !state.name.trim()) {
    blockers.push("Study name is required.");
  }
  if (state.versionIds.length === 0) {
    blockers.push("Select at least one approved strategy version.");
  }
  if (dedupe([...state.assets.map(normalizeTicker), ...listTickers.map(normalizeTicker)]).length === 0) {
    blockers.push("Add at least one asset (or choose a saved list).");
  }
  if (validatePeriod(state.rangeStart, state.rangeEnd, wizardWindows(state)).length > 0) {
    blockers.push("Fix the research period.");
  }
  if (!/^\s*\d+(\.\d+)?\s*$/.test(state.initialCapital) || Number(state.initialCapital) <= 0) {
    blockers.push("Initial capital must be a positive number.");
  }
  return blockers;
}

const FIELD = "mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100";

function VersionPicker({ selected, onToggle, versions }: { selected: string[]; onToggle: (versionId: string) => void; versions: ReturnType<typeof useApiQuery<VersionsBody>> }) {
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
        Choose one or more approved strategies to compare. Older versions remain available.
      </p>
      <ul className="version-grid mt-4 space-y-3 text-sm">
        {families.map((family) => (
          <li key={family.strategy_id} className="version-choice">
            <p className="text-xs font-semibold uppercase text-zinc-400">
              {family.name} <span className="sr-only">family {family.strategy_id.slice(0, 8)}</span> · {family.versions.length} version(s)
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
                    </label>
                    <details><summary>Version details</summary><span className="text-xs text-zinc-500">
                      approved {version.approved_at?.slice(0, 10) ?? "—"} · {version.source.replace(/_/g, " ")} · history {version.history_required} sessions · hash{" "}
                      <span className="font-mono">{version.spec_sha256.slice(0, 12)}</span>
                    </span></details>
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
  const coverage = useApiQuery<CoverageBody>("/api/v1/research/catalog");
  const versions = useApiQuery<VersionsBody>("/api/v1/research/strategies/versions");
  const source = useApiQuery<StudyBody>(fromStudyId ? `/api/v1/research/studies/${encodeURIComponent(fromStudyId)}` : "/api/v1/research/studies?wizard=new");
  const capability = useMutationCapability();
  const [state, setState] = useState<WizardState>(EMPTY_WIZARD);
  const [prefilledFrom, setPrefilledFrom] = useState<string | null>(null);
  const [prefilledRevision, setPrefilledRevision] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);

  // Pre-fill once from the latest revision of the source study; later refetches never
  // overwrite what the user has typed.
  if (fromStudyId && source.result?.ok && prefilledFrom !== fromStudyId) {
    const revisions: Revision[] = source.result.data.study.revisions ?? [];
    const latest = revisions[revisions.length - 1];
    setPrefilledFrom(fromStudyId);
    if (latest) {
      setState(settingsToWizard(latest.settings, source.result.data.study.name));
      setPrefilledRevision(latest.revision_no);
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

  const listsData = lists.result?.ok ? lists.result.data.lists : null;
  const listsFailure = lists.result && !lists.result.ok ? lists.result.message : null;
  const selectedList = listsData?.find((list) => list.list_id === state.assetListId) ?? null;
  const listTickers = selectedList?.tickers ?? [];
  const maxAssets = coverage.result?.ok ? coverage.result.data.catalog.max_assets_per_study : null;
  // Coverage / warm-up preflight for the exact snapshot that will be stored (manual + list),
  // re-run whenever versions, assets, the range or the development start change.
  const allAssets = mergeAssets(state.assets, listTickers);
  const preflight = useStudyPreflight({
    strategy_version_ids: state.versionIds,
    assets: allAssets,
    range: state.rangeStart && state.rangeEnd ? { start: state.rangeStart, end: state.rangeEnd } : null,
    development_start: state.development.start || null,
  });
  const assetCoverage = coverageByTicker(allAssets, preflight.data, preflight.failure);
  const findings = preflight.data ? groupPreflight(preflight.data) : null;
  const endAdvice = endDateAdvice(preflight.data, state.rangeEnd);
  const lastCompletedSession = (preflight.data ?? preflight.last)?.calendar.last_completed_session ?? null;
  const missing = missingInputs(state);
  const removable = removableAssets(allAssets, preflight.data);
  // Confirmed readiness failures: coverage findings the backend reported, or inputs the
  // readiness check is certain to flag. Unknown coverage is not a confirmed failure.
  const confirmedFailures = Boolean(findings && (findings.byAsset.length > 0 || findings.general.length > 0)) || missing.length > 0;
  const createLabel = fromStudyId
    ? confirmedFailures
      ? "Create revision with readiness warnings"
      : "Create new revision"
    : confirmedFailures
      ? "Create study with readiness warnings (revision 1)"
      : "Create study (revision 1)";
  const periodIssues = validatePeriod(state.rangeStart, state.rangeEnd, wizardWindows(state));
  const blockers = wizardBlockers(state, Boolean(fromStudyId), listTickers);
  const disabled = busy || capability.state !== "enabled" || blockers.length > 0;
  const constraintLabel = state.objective === "risk_first" ? "CAGR floor on the validation window (required; may be negative, zero or positive)" : "Max drawdown cap as a magnitude on the validation window (required; e.g. 0.2 means -20%)";
  const revisionNote = fromStudyId
    ? prefilledRevision !== null
      ? `Dates kept exactly from revision ${prefilledRevision}${state.periodMode === "custom" ? " (custom periods preserved to the day)" : " (they match the default preset)"}. Change them only if you mean to.`
      : null
    : null;

  return (
    <div className="space-y-6">
      <div><h1 className="page-heading">{fromStudyId ? "Refine your study" : "Set up a study"}</h1><p className="muted mt-2">Choose what to test, set your assumptions and review data readiness before running.</p></div>
      <div className="wizard-layout">
      <nav aria-label="Study setup sections" className="wizard-nav">
        {["Strategies", "Assets", "Research period", "Risk & costs"].map((label, index) => <a key={label} href={`#setup-${index + 1}`}><span>{index + 1}</span>{label}</a>)}
      </nav>
      <div className="space-y-5">
      <section className="surface-panel"><h2 className="text-sm font-semibold">Give your study a name</h2>        <label className="mt-2 block text-xs text-zinc-400">
          Study name {fromStudyId ? "(kept from the study)" : ""}
          <input value={state.name} onChange={(event) => patch({ name: event.target.value })} className={FIELD} disabled={Boolean(fromStudyId)} />
        </label>
</section>
      {fromStudyId ? (
        <p className="rounded border border-cyan-900 bg-cyan-950/30 px-3 py-2 text-xs text-cyan-100">
          New revision of study <span className="font-mono">{fromStudyId.slice(0, 8)}</span>
          {source.result?.ok ? ` (${source.result.data.study.name})` : ""}: the form is pre-filled from its latest revision. Earlier revisions and their results stay on record.
          {source.result && !source.result.ok ? " The source study could not be loaded; the form starts empty." : ""}
        </p>
      ) : null}

      <StudyAssistant
        current={state}
        isRevision={Boolean(fromStudyId)}
        capability={capability}
        onApply={(update, summary) => {
          patch(update);
          setApplied(summary);
        }}
      />
      {applied ? (
        <p role="status" className="rounded border border-emerald-900 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-100">
          {applied} Review the sections below; nothing is created until you click Create.
        </p>
      ) : null}

      <section id="setup-1" className="surface-panel scroll-mt-6">
        <h2 className="text-sm font-semibold text-zinc-200">1. Choose strategies</h2>
        <VersionPicker selected={state.versionIds} onToggle={toggleVersion} versions={versions} />
        <p className="mt-2 text-xs text-zinc-500">Selected: {state.versionIds.length} version(s).</p>
      </section>

      <section id="setup-2" className="surface-panel scroll-mt-6">
        <h2 className="text-sm font-semibold text-zinc-200">2. Choose assets</h2>
        <AssetPicker
          manual={state.assets}
          onManualChange={(assets) => patch({ assets })}
          listId={state.assetListId}
          onListChange={(assetListId) => patch({ assetListId })}
          lists={listsData}
          listsFailure={listsFailure}
          maxAssets={maxAssets}
          coverage={assetCoverage}
          coverageLoading={preflight.loading}
          findings={findings}
          preflightFailure={preflight.failure}
          removable={removable}
          warmupSessionsMax={preflight.data?.warmup_sessions_max ?? null}
        />
      </section>

      <section id="setup-3" className="surface-panel scroll-mt-6">
        <h2 className="text-sm font-semibold text-zinc-200">3. Research period</h2>
        <ResearchPeriod
          value={{ rangeStart: state.rangeStart, rangeEnd: state.rangeEnd, split: state.split, mode: state.periodMode, windows: wizardWindows(state) }}
          onChange={(next) =>
            patch({
              rangeStart: next.rangeStart,
              rangeEnd: next.rangeEnd,
              split: next.split,
              periodMode: next.mode,
              development: next.windows.development,
              validation: next.windows.validation,
              finalTest: next.windows.final_test,
            })
          }
          issues={periodIssues}
          revisionNote={revisionNote}
          lastCompletedSession={lastCompletedSession}
          endAdvice={endAdvice}
        />
      </section>

      <section id="setup-4" className="surface-panel scroll-mt-6">
        <h2 className="text-sm font-semibold text-zinc-200">4. Set risk, capital and trading costs</h2>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
          <label className="block text-xs text-zinc-400">
            Initial capital
            <input value={state.initialCapital} onChange={(event) => patch({ initialCapital: event.target.value })} className={FIELD} inputMode="decimal" />
          </label>
          <label className="block text-xs text-zinc-400">
            Quantity policy
            <select value={state.quantityPolicy} onChange={(event) => patch({ quantityPolicy: event.target.value as WizardState["quantityPolicy"] })} className={FIELD}>
              <option value="fractional">Fractional shares</option>
              <option value="whole_shares">Whole shares</option>
            </select>
          </label>
          <label className="block text-xs text-zinc-400">
            Objective
            <select value={state.objective} onChange={(event) => patch({ objective: event.target.value as WizardState["objective"] })} className={FIELD}>
              <option value="return_first">Prioritize return · limit drawdown</option>
              <option value="risk_first">Prioritize lower risk · require minimum growth</option>
            </select>
          </label>
          <label className="block text-xs text-zinc-400">
            Slippage, bps per side (required; no default — the engine&apos;s 5 bps is only an example)
            <input required value={state.slippageBps} onChange={(event) => patch({ slippageBps: event.target.value })} className={FIELD} placeholder="enter a value" inputMode="decimal" aria-describedby={state.slippageBps.trim() === "" ? "warn-slippage" : undefined} />
            {state.slippageBps.trim() === "" ? (
              <span id="warn-slippage" className="mt-0.5 block text-amber-300">
                Not entered: the revision will report costs_missing until it is.
              </span>
            ) : null}
          </label>
          <label className="block text-xs text-zinc-400">
            Commission per order (required; no default — 0 is only an example)
            <input required value={state.commission} onChange={(event) => patch({ commission: event.target.value })} className={FIELD} placeholder="enter a value" inputMode="decimal" aria-describedby={state.commission.trim() === "" ? "warn-commission" : undefined} />
            {state.commission.trim() === "" ? (
              <span id="warn-commission" className="mt-0.5 block text-amber-300">
                Not entered: the revision will report costs_missing until it is (0 is accepted).
              </span>
            ) : null}
          </label>
          <label className="block text-xs text-zinc-400">
            {constraintLabel}
            <input required value={state.constraintValue} onChange={(event) => patch({ constraintValue: event.target.value })} className={FIELD} placeholder="enter a value (no approved default)" inputMode="decimal" aria-describedby={state.constraintValue.trim() === "" ? "warn-constraint" : undefined} />
            {state.constraintValue.trim() === "" ? (
              <span id="warn-constraint" className="mt-0.5 block text-amber-300">
                Not entered: the revision will report constraint_missing until the {state.objective === "risk_first" ? "CAGR floor" : "drawdown cap"} is set.
              </span>
            ) : null}
          </label>
        </div>
        <p className="mt-1 text-xs text-zinc-500">Costs and the constraint value are your inputs; a missing one makes the revision not ready (`costs_missing`, `constraint_missing`) instead of being filled in.</p>
        <label className="mt-2 block text-xs text-zinc-400">
          Note (optional)
          <input value={state.note} onChange={(event) => patch({ note: event.target.value })} className={FIELD} />
        </label>
        {!fromStudyId ? (
          <details className="mt-4"><summary>Advanced: pipeline test</summary><label className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
            <input type="checkbox" checked={state.kind === "smoke"} onChange={(event) => patch({ kind: event.target.checked ? "smoke" : "substantive" })} />
            Smoke run: an integration test of the pipeline, labelled as such everywhere; its settings are test configuration, not research objectives, and its outcome is not a research claim.
          </label></details>
        ) : null}
      </section>

      <div className="wizard-actions flex flex-wrap items-center gap-3">
        <button type="button" onClick={submit} disabled={disabled} className="button-primary">
          {createLabel}
        </button>
        <span className="text-xs text-zinc-500">Creating does not run anything; the readiness screen lists every problem first.</span>
        {capability.state !== "enabled" ? <span className="text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</span> : null}
      </div>
      {blockers.length === 0 && missing.length > 0 ? (
        <div role="status" className="rounded border border-amber-900 bg-amber-950/30 px-3 py-2 text-xs text-amber-100" data-testid="not-ready-summary">
          <p className="font-semibold">This revision can be created, but will not be ready to run.</p>
          <ul className="mt-1 list-disc pl-5">
            {missing.map((item) => (
              <li key={item.key}>
                {item.label}: {item.detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {blockers.length > 0 ? (
        <ul className="list-disc pl-5 text-xs text-amber-200" aria-label="Before creating">
          {blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      ) : null}
      {message ? (
        <p role="alert" className="rounded border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-100">
          {message} Your entries are kept.
        </p>
      ) : null}
      </div>
      </div>
    </div>
  );
}
