"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { approveDraft, createDraft, deleteDraft, duplicateDraft, updateDraft, validateYamlText } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import type { Draft, StrategyVersion, ValidationOutcome } from "@/lib/research/types";
import { ExplanationPanel, ValidationPanel } from "./ValidationPanel";
import { AssistantPanel } from "./AssistantPanel";

const STARTER_YAML = `spec_version: 1
name: My strategy
description: Describe the idea in one or two sentences.
timeframe: daily
direction: long_only
indicators:
  sma_fast: {type: sma, source: close, window: 50}
  sma_slow: {type: sma, source: close, window: 200}
entry:
  all_of:
    - {left: close, op: gt, right: sma_slow}
    - {left: sma_fast, op: gt, right: sma_slow}
exit:
  any_of:
    - {left: close, op: lt, right: sma_fast}
`;

type Outcome = { kind: "idle" } | { kind: "error"; message: string } | { kind: "info"; message: string };

/**
 * YAML editor for one draft (or a new one when `draftId` is null). Validation runs against
 * the API on every change (debounced), the explanation is the deterministic rendering of the
 * specification, saving and approval are explicit actions. The typed text is never discarded
 * on a failed request: the editor keeps its state and shows the typed error beside it.
 */
export function DraftEditor({ draftId, onNavigate }: { draftId: string | null; onNavigate: (href: string) => void }) {
  const endpoint = draftId ? `/api/v1/research/strategies/drafts/${encodeURIComponent(draftId)}` : null;
  const query = useApiQuery<{ draft: Draft }>(endpoint ?? "/api/v1/research/strategies/drafts?editor=new");
  const capability = useMutationCapability();
  const [title, setTitle] = useState<string>("");
  const [yaml, setYaml] = useState<string>(draftId ? "" : STARTER_YAML);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationOutcome | null>(null);
  const [validatedText, setValidatedText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const [confirmApprove, setConfirmApprove] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load the draft text once per draft id; later refetches never overwrite what is typed.
  if (draftId && query.result?.ok && loadedFor !== draftId) {
    setLoadedFor(draftId);
    setTitle(query.result.data.draft.title);
    setYaml(query.result.data.draft.yaml_text);
  }

  useEffect(() => {
    if (timer.current) {
      clearTimeout(timer.current);
    }
    const text = yaml;
    if (!text.trim()) {
      return;
    }
    timer.current = setTimeout(async () => {
      const result = await validateYamlText(text);
      if (result.ok) {
        setValidation((result.data as { validation: ValidationOutcome }).validation);
        setValidatedText(text);
      }
    }, 400);
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
      }
    };
  }, [yaml]);

  const disabled = busy || capability.state !== "enabled";
  const disabledReason = capability.state === "enabled" ? null : capability.reason ?? "Mutation availability unknown";

  async function save() {
    setBusy(true);
    setOutcome({ kind: "idle" });
    const result = draftId
      ? await updateDraft(draftId, { title, yaml_text: yaml })
      : await createDraft({ title: title || "Untitled draft", yaml_text: yaml });
    setBusy(false);
    if (!result.ok) {
      setOutcome({ kind: "error", message: result.message });
      return;
    }
    if (!draftId) {
      const created = (result.data as { draft: Draft }).draft;
      onNavigate(`/research/strategies/drafts/${created.draft_id}`);
      return;
    }
    setOutcome({ kind: "info", message: "Draft saved." });
    query.refetch();
  }

  async function approve() {
    if (!draftId) {
      return;
    }
    setBusy(true);
    const saved = await updateDraft(draftId, { title, yaml_text: yaml });
    if (!saved.ok) {
      setBusy(false);
      setOutcome({ kind: "error", message: saved.message });
      return;
    }
    const result = await approveDraft(draftId);
    setBusy(false);
    setConfirmApprove(false);
    if (!result.ok) {
      setOutcome({ kind: "error", message: result.message });
      return;
    }
    const version = (result.data as { version: StrategyVersion }).version;
    onNavigate(`/research/strategies/versions/${version.version_id}`);
  }

  async function duplicate() {
    if (!draftId) {
      return;
    }
    setBusy(true);
    const result = await duplicateDraft(draftId);
    setBusy(false);
    if (!result.ok) {
      setOutcome({ kind: "error", message: result.message });
      return;
    }
    onNavigate(`/research/strategies/drafts/${(result.data as { draft: Draft }).draft.draft_id}`);
  }

  async function remove() {
    if (!draftId) {
      return;
    }
    setBusy(true);
    const result = await deleteDraft(draftId);
    setBusy(false);
    if (!result.ok) {
      setOutcome({ kind: "error", message: result.message });
      return;
    }
    onNavigate("/research/strategies");
  }

  if (draftId && query.result && !query.result.ok && loadedFor !== draftId) {
    return <ErrorState failure={query.result} title="Draft could not be loaded" />;
  }

  const pending = validatedText !== null && validatedText !== yaml;
  const canApprove = Boolean(draftId) && validation?.valid === true && !pending;
  const provenance = draftId && query.result?.ok ? query.result.data.draft.assistant : null;

  return (
    <div className="space-y-4">
    <header>
      <p className="eyebrow">Strategy research</p>
      <h1 className="page-heading">{draftId ? "Refine your strategy" : "Build a strategy"}</h1>
      <p className="muted mt-1 text-sm">Describe the idea in your own words. Review the rules and validation before saving a draft.</p>
    </header>
    <AssistantPanel
      draftId={draftId}
      editorYaml={yaml}
      editorIsStarter={yaml === STARTER_YAML}
      capability={capability}
      onUseInEditor={(text) => {
        setYaml(text);
        setOutcome({ kind: "info", message: "Proposal copied into the editor; save the draft to keep it." });
      }}
      onApplied={(draft) => {
        if (!draftId) {
          onNavigate(`/research/strategies/drafts/${draft.draft_id}`);
          return;
        }
        setYaml(draft.yaml_text);
        setTitle(draft.title);
        setOutcome({ kind: "info", message: "Assistant proposal applied to this draft (saved)." });
        query.refetch();
      }}
    />

    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(300px,.75fr)]">
      <section className="surface-panel space-y-4">
        <div>
          <p className="eyebrow">Your working copy</p>
          <h2 className="text-lg font-semibold">Draft details</h2>
          <p className="muted mt-1 text-sm">Review the plain-language explanation and validation before saving or approving.</p>
        </div>
        <label className="block text-sm">
          <span className="font-medium">Draft name</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[var(--foreground)]"
            placeholder="Name this strategy"
          />
        </label>
        <div className="surface-muted rounded-xl p-4">
          <p className="eyebrow">What happens next</p>
          <p className="mt-1 text-sm">A valid draft can be saved and reviewed. Approval creates a new immutable strategy version for studies.</p>
        </div>
        <details className="rounded-xl border border-[var(--border)] p-4">
          <summary className="cursor-pointer font-medium">Advanced: example rules and YAML editor</summary>
          <p className="muted mt-2 text-sm">Optional. The starter is an example to edit or replace; it is not saved until you choose Save draft. Most people can build and revise the strategy with the assistant above.</p>
          <label className="mt-4 block text-sm">
            <span className="font-medium">Specification (YAML)</span>
            <textarea
              value={yaml}
              onChange={(event) => setYaml(event.target.value)}
              spellCheck={false}
              rows={24}
              className="mt-2 w-full rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 font-mono text-xs leading-6 text-[var(--foreground)]"
            />
          </label>
        </details>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={save} disabled={disabled} className="button-secondary disabled:opacity-50">{draftId ? "Save draft" : "Create draft"}</button>
          {draftId ? (
            <>
              <button type="button" onClick={() => setConfirmApprove(true)} disabled={disabled || !canApprove} className="button-primary disabled:opacity-50">Approve immutable version…</button>
              <button type="button" onClick={duplicate} disabled={disabled} className="button-secondary disabled:opacity-50">Duplicate draft</button>
              <button type="button" onClick={remove} disabled={disabled} className="button-secondary border-[var(--negative)]/40 text-[var(--negative)] disabled:opacity-50">Delete draft</button>
            </>
          ) : null}
          {disabledReason ? <span className="muted text-xs">{disabledReason}</span> : null}
        </div>
        {!canApprove && draftId ? <p className="muted text-xs">Approval needs a valid specification whose validation matches the current text.</p> : null}
        {confirmApprove ? (
          <div className="rounded-xl border border-[var(--warning)]/40 bg-[var(--warning)]/10 p-4 text-sm">
            <p>Approve this specification as an immutable version? It cannot be edited or deleted. Later changes create another draft.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" onClick={approve} disabled={disabled} className="button-primary disabled:opacity-50">Confirm approval</button>
              <button type="button" onClick={() => setConfirmApprove(false)} className="button-secondary">Cancel</button>
            </div>
          </div>
        ) : null}
        {outcome.kind === "error" ? (
          <p role="alert" className="rounded-lg border border-[var(--negative)]/30 bg-[var(--negative)]/5 px-3 py-2 text-sm text-[var(--negative)]">
            {outcome.message} Your text is kept.
          </p>
        ) : null}
        {outcome.kind === "info" ? <p className="text-sm text-[var(--positive)]">{outcome.message}</p> : null}
        {draftId && query.result?.ok && query.result.data.draft.parent_version_id ? (
          <details className="muted text-sm">
            <summary className="cursor-pointer">Draft lineage</summary>
            <p className="mt-2">Derived from <Link href={`/research/strategies/versions/${query.result.data.draft.parent_version_id}`} className="text-[var(--accent)] hover:underline">version {query.result.data.draft.parent_version_id.slice(0, 8)}</Link> ({query.result.data.draft.source.replace(/_/g, " ")}).</p>
          </details>
        ) : null}
      </section>
      <div className="space-y-4">
        <ExplanationPanel explanation={validation?.valid && !pending ? validation.explanation : null} />
        <ValidationPanel outcome={validation} pending={pending} example={!draftId && yaml === STARTER_YAML} />
      </div>
    </div>
    {provenance ? (
      <details className="surface-panel" data-testid="draft-provenance">
        <summary className="cursor-pointer font-medium">Assistant history · {provenance.requests} request{provenance.requests === 1 ? "" : "s"}</summary>
        <p className="muted mt-2 text-sm">{provenance.summary.replace(", approved by user", "")}</p>
        <ol className="mt-3 space-y-2">
          {provenance.attempts.map((attempt) => (
            <li key={attempt.ai_draft_id} className="surface-muted rounded-lg p-3 text-xs">
              <div className="font-medium">{attempt.kind} · attempt {attempt.attempt_no} · {attempt.status}</div>
              <div className="muted mt-1">{attempt.provider}/{attempt.model} · prompt {attempt.prompt_version} · {attempt.completed_at ?? "In progress"}</div>
              <p className="mt-2 whitespace-pre-wrap">{attempt.user_text}</p>
              {attempt.note ? <p className="muted mt-1">Assistant note: {attempt.note}</p> : null}
            </li>
          ))}
        </ol>
      </details>
    ) : null}
    </div>
  );
}
