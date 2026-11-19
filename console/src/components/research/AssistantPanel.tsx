"use client";

import { useEffect, useRef, useState } from "react";
import { applyAssistantProposal, requestAssistantProposal } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import type { MutationCapability } from "@/lib/useMutationCapability";
import type { AssistantProposal, AssistantStatus, Draft } from "@/lib/research/types";
import { ExplanationPanel, ValidationPanel } from "./ValidationPanel";

const BOX = "surface-panel";
const FIELD = "mt-2 w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 text-sm text-[var(--foreground)] placeholder:text-[var(--muted)]";

type Mode = "draft" | "revise";

type Props = {
  draftId: string | null;
  editorYaml: string;
  editorIsStarter: boolean;
  capability: MutationCapability;
  onUseInEditor: (yaml: string) => void;
  onApplied: (draft: Draft) => void;
};

function newToken(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `tok-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * S5 assistant panel (proposal E.2): describe a strategy, or ask for a correction of the
 * YAML in the editor, and inspect what comes back: the proposed YAML (editable), the
 * validator's findings, the assistant's unsupported-request list, its note (labelled as
 * the assistant's) and the deterministic explanation. Nothing touches the draft until
 * "Apply as draft"; "Use in editor" only copies text into the editor. Approval stays the
 * editor's own action. A failed request keeps the typed text and the last proposal.
 * Every click carries a fresh request token, so a repeated click or a lost response
 * cannot run the request twice.
 */
export function AssistantPanel({ draftId, editorYaml, editorIsStarter, capability, onUseInEditor, onApplied }: Props) {
  const statusEndpoint = draftId
    ? `/api/v1/research/assistant?draft_id=${encodeURIComponent(draftId)}`
    : "/api/v1/research/assistant";
  const status = useApiQuery<{ assistant: AssistantStatus }>(statusEndpoint);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>(draftId || !editorIsStarter ? "revise" : "draft");
  const [proposal, setProposal] = useState<AssistantProposal | null>(null);
  const [proposalYaml, setProposalYaml] = useState("");
  const [busy, setBusy] = useState<"propose" | "apply" | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    if (startedAt === null) {
      return;
    }
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  const elapsed = startedAt === null ? 0 : Math.max(0, Math.round((now - startedAt) / 1000));

  const assistant = status.result?.ok ? status.result.data.assistant : null;
  const statusFailure = status.result && !status.result.ok ? status.result : null;
  const exhausted = assistant ? assistant.usage.remaining_today <= 0 : false;
  const revisionCapped =
    assistant && assistant.usage.revisions_used !== null
      ? assistant.usage.revisions_used >= assistant.limits.max_revisions_per_draft
      : false;
  const mutationsBlocked = capability.state !== "enabled";
  const canAsk =
    Boolean(assistant?.configured) &&
    !exhausted &&
    !mutationsBlocked &&
    busy === null &&
    text.trim().length > 0 &&
    !(mode === "revise" && draftId !== null && revisionCapped);
  const edited = proposal !== null && proposalYaml !== (proposal.yaml_text ?? "");

  async function ask() {
    if (!canAsk || inFlight.current) {
      return;
    }
    inFlight.current = true;
    setBusy("propose");
    setStartedAt(Date.now());
    setNow(Date.now());
    setError(null);
    setInfo(null);
    const body: Parameters<typeof requestAssistantProposal>[0] = { user_text: text.trim(), request_token: newToken() };
    if (mode === "revise") {
      // Revise what the user is looking at: the (possibly edited) proposal, else the editor's YAML.
      body.base_yaml_text = proposal ? proposalYaml : editorYaml;
      if (draftId) {
        body.draft_id = draftId;
      }
      if (proposal) {
        body.parent_ai_draft_id = proposal.ai_draft_id;
      }
    }
    const result = await requestAssistantProposal(body);
    inFlight.current = false;
    setBusy(null);
    setStartedAt(null);
    status.refetch();
    if (!result.ok) {
      setError(result.message);
      return;
    }
    const next = (result.data as { proposal: AssistantProposal }).proposal;
    setProposal(next);
    setProposalYaml(next.yaml_text ?? "");
    setText("");
  }

  async function apply() {
    if (!proposal || busy !== null || edited || mutationsBlocked) {
      return;
    }
    setBusy("apply");
    setError(null);
    const result = await applyAssistantProposal(proposal.ai_draft_id, draftId ? { draft_id: draftId } : {});
    setBusy(null);
    if (!result.ok) {
      setError(result.message);
      return;
    }
    const data = result.data as { draft: Draft; proposal: AssistantProposal; already_applied: boolean };
    setProposal(data.proposal);
    setInfo(data.already_applied ? "This proposal was already applied to the draft." : "Proposal applied to the draft.");
    onApplied(data.draft);
  }

  return (
    <section aria-label="Assistant" className={`${BOX} space-y-4 border-[var(--accent)]/25`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="eyebrow">Start with your idea</p>
          <h2 className="text-xl font-semibold">Describe your strategy</h2>
          <p className="muted mt-1 max-w-2xl text-sm">Use everyday language to create or revise rules. Inspect the proposal, validation, and plain-language explanation before saving or approving.</p>
        </div>
        {assistant ? (
          <div className="surface-muted rounded-lg px-3 py-2 text-xs">
            <span className="font-medium">Today&apos;s requests</span>
            <span className="muted ml-2">{assistant.usage.requests_today} of {assistant.limits.max_requests_per_day}</span>
            {assistant.usage.revisions_used !== null ? <span className="muted ml-2">· {assistant.usage.revisions_used}/{assistant.limits.max_revisions_per_draft} revisions on this draft</span> : null}
            {assistant.usage.in_flight > 0 ? <span className="muted ml-2">· {assistant.usage.in_flight} in progress</span> : null}
          </div>
        ) : null}
      </div>

      {statusFailure ? (
        <p role="alert" className="rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 p-3 text-sm">
          Assistant status unavailable ({statusFailure.status ?? "unreachable"}): {statusFailure.message}. The editor keeps working.
        </p>
      ) : null}

      {assistant && !assistant.configured ? (
        <div role="status" className="rounded-xl border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-4 py-3 text-sm">
          <p className="font-semibold">{assistant.enabled ? "The assistant needs configuration." : "The assistant is currently disabled."}</p>
          <p className="mt-1">You can still write, validate, and approve a strategy using the advanced editor. To activate the assistant, set these API environment values and restart:</p>
          <ul className="mt-2 list-disc pl-5 font-mono text-xs">
            {assistant.missing.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="muted mt-2 text-xs">{assistant.note}</p>
        </div>
      ) : null}

      {assistant?.configured ? (
        <details className="muted text-xs">
          <summary className="cursor-pointer">Assistant limits and provider details</summary>
          <p className="mt-2">
          Limits: {assistant.limits.max_requests_per_day} requests/day (retries count), {assistant.limits.max_output_tokens} output tokens,{" "}
          {assistant.limits.max_input_characters} input characters, {assistant.limits.max_revisions_per_draft} revisions per draft,{" "}
          {assistant.limits.max_concurrent_requests} concurrent, {assistant.limits.timeout_seconds}s deadline, {assistant.limits.automatic_retries_on_invalid_output} automatic retry on invalid output. {assistant.note}
          </p>
          <p className="mt-1">Provider: {assistant.provider}/{assistant.model} · prompt {assistant.prompt_version}.</p>
        </details>
      ) : null}

      {exhausted && assistant?.configured ? (
        <p role="status" className="rounded-lg bg-[var(--warning)]/10 p-3 text-sm">
          Today&apos;s allowance is used up ({assistant.usage.requests_today}/{assistant.limits.max_requests_per_day}). The editor keeps working.
        </p>
      ) : null}

      <fieldset className="flex flex-wrap items-center gap-3 text-sm">
        <legend className="sr-only">Choose how to use the assistant</legend>
        <label className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 ${mode === "draft" ? "border-[var(--accent)] bg-[var(--accent)]/5" : "border-[var(--border)]"}`}>
          <input type="radio" name="assistant-mode" value="draft" checked={mode === "draft"} onChange={() => setMode("draft")} disabled={busy !== null} />
          Draft a new strategy from a description
        </label>
        <label className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 ${mode === "revise" ? "border-[var(--accent)] bg-[var(--accent)]/5" : "border-[var(--border)]"}`}>
          <input type="radio" name="assistant-mode" value="revise" checked={mode === "revise"} onChange={() => setMode("revise")} disabled={busy !== null} />
          Revise the current strategy with a description
        </label>
      </fieldset>

      <label className="block text-sm">
        <span className="font-medium">{mode === "draft" ? "What should the strategy do?" : "What would you like to change?"}</span>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={3}
          disabled={busy !== null || !assistant?.configured}
          className={FIELD}
          placeholder={mode === "draft" ? "For example: Buy when the 50-day average moves above the 200-day average, and exit when the price closes below the 50-day average." : "For example: Use a 60-day average and exit only after the price closes 2% below it."}
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={ask} disabled={!canAsk} className="button-primary min-h-11 disabled:cursor-not-allowed disabled:opacity-50">
          {busy === "propose" ? `Asking the assistant… ${elapsed}s${assistant ? ` of ${assistant.limits.timeout_seconds}s` : ""}` : mode === "draft" ? "Draft with the assistant" : "Request a revision"}
        </button>
        {mutationsBlocked ? <span className="muted text-xs">{capability.reason ?? "Mutation availability unknown"}</span> : null}
        {mode === "revise" && draftId && revisionCapped ? <span className="text-xs text-[var(--warning)]">Revision limit reached for this draft.</span> : null}
      </div>

      {error ? (
        <p role="alert" className="rounded-lg border border-[var(--negative)]/30 bg-[var(--negative)]/5 px-3 py-2 text-sm text-[var(--negative)]">
          {error} Your text and the last proposal are kept.
        </p>
      ) : null}
      {info ? <p className="text-sm text-[var(--positive)]">{info}</p> : null}

      {proposal ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" data-testid="assistant-proposal">
          <div className="space-y-2">
            <p className="muted text-xs">
              Proposal {proposal.ai_draft_id.slice(0, 8)} · {proposal.kind} · attempt {proposal.attempt_no} ·{" "}
              <span className={proposal.status === "ok" ? "text-[var(--positive)]" : "text-[var(--warning)]"}>{proposal.status}</span>
              {proposal.failure_code ? ` (${proposal.failure_code.replace(/_/g, " ")})` : ""} · {proposal.provenance.model} · request {proposal.provenance.request_id ?? "—"} · tokens{" "}
              {proposal.provenance.input_tokens ?? "?"}/{proposal.provenance.output_tokens ?? "?"}
              {proposal.applied_at ? " · applied" : ""}
            </p>
            {proposal.status === "invalid" ? (
              <p role="status" className="rounded-lg bg-[var(--warning)]/10 p-3 text-sm">
                The assistant&apos;s output failed validation twice (one automatic retry). The findings are listed; edit the YAML or ask again.
              </p>
            ) : null}
            <div className="surface-muted rounded-xl p-3 text-sm">
              <p className="eyebrow">Assistant&apos;s note</p>
              <p className="mt-1">{proposal.note || "—"}</p>
              <p className="muted mt-2 text-xs">Separate from the rule explanation generated from the specification.</p>
            </div>
            <div className="surface-muted rounded-xl p-3 text-sm">
              <p className="eyebrow">Requests the assistant could not represent ({proposal.unsupported_requests.length})</p>
              {proposal.unsupported_requests.length === 0 ? (
                <p className="muted mt-1">None reported. A valid result does not confirm the rules match your intent; read the explanation carefully.</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {proposal.unsupported_requests.map((item, index) => (
                    <li key={`${item.code}-${index}`}>
                      <code className="text-[var(--warning)]">{item.code}</code> <span>{item.detail}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <label className="block text-sm">
              <details className="rounded-xl border border-[var(--border)] p-3">
                <summary className="cursor-pointer font-medium">Advanced: inspect or edit the proposed rules</summary>
                <p className="muted mt-2 text-xs">Optional. The plain-language explanation and validation above are the main review tools.</p>
                <label className="mt-3 block text-sm">
                  <span className="font-medium">Proposed specification (advanced YAML)</span>
                  <textarea
                    value={proposalYaml}
                    onChange={(event) => setProposalYaml(event.target.value)}
                    spellCheck={false}
                    rows={16}
                    className="mt-2 w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 font-mono text-xs leading-6 text-[var(--foreground)]"
                  />
                </label>
              </details>
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => onUseInEditor(proposalYaml)} disabled={busy !== null || !proposalYaml} className="button-secondary disabled:opacity-50">
                Use in editor
              </button>
              <button type="button" onClick={apply} disabled={busy !== null || edited || !proposal.yaml_text || mutationsBlocked} className="button-primary disabled:opacity-50">
                {busy === "apply" ? "Applying…" : draftId ? "Apply as this draft's YAML" : "Apply as a new draft"}
              </button>
              {edited ? (
                <span className="muted text-xs">
                  You edited the proposal: use it in the editor and save, or{" "}
                    <button type="button" onClick={() => setProposalYaml(proposal.yaml_text ?? "")} className="text-[var(--accent)] underline">
                    reset to the proposal
                  </button>
                  .
                </span>
              ) : (
                <span className="muted text-xs">Applying saves the draft; approval is a separate action.</span>
              )}
            </div>
          </div>
          <div className="space-y-4">
            <ValidationPanel outcome={proposal.validation} pending={edited} />
            <ExplanationPanel explanation={!edited && proposal.validation?.valid ? proposal.explanation : null} />
          </div>
        </div>
      ) : null}
    </section>
  );
}
