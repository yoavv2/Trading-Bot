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
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <section className="space-y-3">
        <label className="block text-sm">
          <span className="text-zinc-400">Title</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-100"
            placeholder="Draft title"
          />
        </label>
        <label className="block text-sm">
          <span className="text-zinc-400">Specification (YAML)</span>
          <textarea
            value={yaml}
            onChange={(event) => setYaml(event.target.value)}
            spellCheck={false}
            rows={26}
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 font-mono text-xs text-zinc-100"
          />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={save} disabled={disabled} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800 disabled:opacity-50">
            {draftId ? "Save draft" : "Create draft"}
          </button>
          {draftId ? (
            <>
              <button type="button" onClick={() => setConfirmApprove(true)} disabled={disabled || !canApprove} className="rounded border border-emerald-700 px-3 py-1 text-sm text-emerald-200 hover:bg-emerald-950 disabled:opacity-50">
                Approve as immutable version…
              </button>
              <button type="button" onClick={duplicate} disabled={disabled} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800 disabled:opacity-50">
                Duplicate
              </button>
              <button type="button" onClick={remove} disabled={disabled} className="rounded border border-red-800 px-3 py-1 text-sm text-red-200 hover:bg-red-950 disabled:opacity-50">
                Delete draft
              </button>
            </>
          ) : null}
          {disabledReason ? <span className="text-xs text-zinc-500">{disabledReason}</span> : null}
        </div>
        {!canApprove && draftId ? (
          <p className="text-xs text-zinc-500">Approval needs a valid specification whose validation matches the current text.</p>
        ) : null}
        {confirmApprove ? (
          <div className="rounded border border-emerald-800 bg-emerald-950/30 p-3 text-sm">
            <p className="text-emerald-100">
              Approve this specification as an immutable version? The version can never be edited or deleted; editing later creates a new draft.
            </p>
            <div className="mt-2 flex gap-2">
              <button type="button" onClick={approve} disabled={disabled} className="rounded border border-emerald-600 px-3 py-1 text-emerald-100 hover:bg-emerald-900 disabled:opacity-50">
                Approve
              </button>
              <button type="button" onClick={() => setConfirmApprove(false)} className="rounded border border-zinc-600 px-3 py-1 text-zinc-300">
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {outcome.kind === "error" ? (
          <p role="alert" className="rounded border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-100">
            {outcome.message} Your text is kept.
          </p>
        ) : null}
        {outcome.kind === "info" ? <p className="text-sm text-emerald-300">{outcome.message}</p> : null}
        {provenance ? (
          <p className="text-xs text-zinc-500" data-testid="draft-provenance">
            {provenance.summary.replace(", approved by user", "")} · {provenance.attempts[0]?.provider}/{provenance.attempts[0]?.model} · prompt {provenance.attempts[0]?.prompt_version}
          </p>
        ) : null}
        {draftId && query.result?.ok && query.result.data.draft.parent_version_id ? (
          <p className="text-xs text-zinc-500">
            Derived from version{" "}
            <Link href={`/research/strategies/versions/${query.result.data.draft.parent_version_id}`} className="text-cyan-300">
              {query.result.data.draft.parent_version_id.slice(0, 8)}
            </Link>{" "}
            ({query.result.data.draft.source.replace(/_/g, " ")}).
          </p>
        ) : null}
      </section>
      <div className="space-y-4">
        <ValidationPanel outcome={validation} pending={pending} />
        <ExplanationPanel explanation={validation?.valid && !pending ? validation.explanation : null} />
      </div>
    </div>
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
    </div>
  );
}
