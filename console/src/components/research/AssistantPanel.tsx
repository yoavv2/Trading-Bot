"use client";

import { useEffect, useRef, useState } from "react";
import { applyAssistantProposal, requestAssistantProposal } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import type { MutationCapability } from "@/lib/useMutationCapability";
import type { AssistantProposal, AssistantStatus, Draft } from "@/lib/research/types";
import { ExplanationPanel, ValidationPanel } from "./ValidationPanel";

const BOX = "rounded border border-zinc-800 bg-zinc-900/40 p-3";
const FIELD = "mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-sm text-zinc-100";

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
    <section aria-label="Assistant" className={`${BOX} space-y-3`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-200">Assistant (drafts and revisions; you review and approve)</h2>
        {assistant ? (
          <span className="text-xs text-zinc-500">
            {assistant.provider}/{assistant.model} · prompt {assistant.prompt_version} · today {assistant.usage.requests_today}/{assistant.limits.max_requests_per_day} requests
            {assistant.usage.revisions_used !== null ? ` · this draft ${assistant.usage.revisions_used}/${assistant.limits.max_revisions_per_draft} revisions` : ""}
            {assistant.usage.in_flight > 0 ? ` · ${assistant.usage.in_flight} in flight` : ""}
          </span>
        ) : null}
      </div>

      {statusFailure ? (
        <p role="alert" className="text-sm text-amber-200">
          Assistant status unavailable ({statusFailure.status ?? "unreachable"}): {statusFailure.message}. The editor keeps working.
        </p>
      ) : null}

      {assistant && !assistant.configured ? (
        <div role="status" className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-100">
          <p className="font-semibold">{assistant.enabled ? "The assistant is enabled but not configured." : "The assistant is disabled."}</p>
          <p className="mt-1">The editor, validation and approval work without it. To activate it, set in the API environment and restart:</p>
          <ul className="mt-1 list-disc pl-5 font-mono text-xs">
            {assistant.missing.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-amber-200/80">{assistant.note}</p>
        </div>
      ) : null}

      {assistant?.configured ? (
        <p className="text-xs text-zinc-500">
          Limits: {assistant.limits.max_requests_per_day} requests/day (retries count), {assistant.limits.max_output_tokens} output tokens,{" "}
          {assistant.limits.max_input_characters} input characters, {assistant.limits.max_revisions_per_draft} revisions per draft,{" "}
          {assistant.limits.max_concurrent_requests} concurrent, {assistant.limits.timeout_seconds}s deadline, {assistant.limits.automatic_retries_on_invalid_output} automatic retry on invalid output. {assistant.note}
        </p>
      ) : null}

      {exhausted && assistant?.configured ? (
        <p role="status" className="text-sm text-amber-200">
          Today&apos;s allowance is used up ({assistant.usage.requests_today}/{assistant.limits.max_requests_per_day}). The editor keeps working.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1">
          <input type="radio" name="assistant-mode" value="draft" checked={mode === "draft"} onChange={() => setMode("draft")} disabled={busy !== null} />
          Draft a new strategy from a description
        </label>
        <label className="flex items-center gap-1">
          <input type="radio" name="assistant-mode" value="revise" checked={mode === "revise"} onChange={() => setMode("revise")} disabled={busy !== null} />
          Revise the {proposal ? "proposal" : "editor's YAML"} with a correction
        </label>
      </div>

      <label className="block text-sm">
        <span className="text-zinc-400">{mode === "draft" ? "Describe the strategy" : "Describe the correction"}</span>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={3}
          disabled={busy !== null || !assistant?.configured}
          className={FIELD}
          placeholder={mode === "draft" ? "e.g. go long when the 50-day average crosses above the 200-day average; exit when the close falls below the 50-day average" : "e.g. use a 60-day average instead of 50 and exit only when the close is 2% below it"}
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={ask} disabled={!canAsk} className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950 disabled:opacity-50">
          {busy === "propose" ? `Asking the assistant… ${elapsed}s${assistant ? ` of ${assistant.limits.timeout_seconds}s` : ""}` : mode === "draft" ? "Draft with the assistant" : "Request a revision"}
        </button>
        {mutationsBlocked ? <span className="text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</span> : null}
        {mode === "revise" && draftId && revisionCapped ? <span className="text-xs text-amber-200">Revision limit reached for this draft.</span> : null}
      </div>

      {error ? (
        <p role="alert" className="rounded border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-100">
          {error} Your text and the last proposal are kept.
        </p>
      ) : null}
      {info ? <p className="text-sm text-emerald-300">{info}</p> : null}

      {proposal ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" data-testid="assistant-proposal">
          <div className="space-y-2">
            <p className="text-xs text-zinc-500">
              Proposal {proposal.ai_draft_id.slice(0, 8)} · {proposal.kind} · attempt {proposal.attempt_no} ·{" "}
              <span className={proposal.status === "ok" ? "text-emerald-300" : "text-amber-200"}>{proposal.status}</span>
              {proposal.failure_code ? ` (${proposal.failure_code.replace(/_/g, " ")})` : ""} · {proposal.provenance.model} · request {proposal.provenance.request_id ?? "—"} · tokens{" "}
              {proposal.provenance.input_tokens ?? "?"}/{proposal.provenance.output_tokens ?? "?"}
              {proposal.applied_at ? " · applied" : ""}
            </p>
            {proposal.status === "invalid" ? (
              <p role="status" className="text-sm text-amber-200">
                The assistant&apos;s output failed validation twice (one automatic retry). The findings are listed; edit the YAML or ask again.
              </p>
            ) : null}
            <div className="rounded border border-zinc-800 p-2 text-sm">
              <p className="text-xs uppercase text-zinc-500">Assistant&apos;s note (not the explanation)</p>
              <p className="mt-1 text-zinc-300">{proposal.note || "—"}</p>
            </div>
            <div className="rounded border border-zinc-800 p-2 text-sm">
              <p className="text-xs uppercase text-zinc-500">Unsupported requests ({proposal.unsupported_requests.length})</p>
              {proposal.unsupported_requests.length === 0 ? (
                <p className="mt-1 text-zinc-500">None reported. Validity does not prove the strategy matches your intent: read the explanation.</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {proposal.unsupported_requests.map((item, index) => (
                    <li key={`${item.code}-${index}`}>
                      <code className="text-amber-200">{item.code}</code> <span className="text-zinc-300">{item.detail}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <label className="block text-sm">
              <span className="text-zinc-400">Proposed specification (YAML, editable)</span>
              <textarea
                value={proposalYaml}
                onChange={(event) => setProposalYaml(event.target.value)}
                spellCheck={false}
                rows={16}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 font-mono text-xs text-zinc-100"
              />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => onUseInEditor(proposalYaml)} disabled={busy !== null || !proposalYaml} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800 disabled:opacity-50">
                Use in editor
              </button>
              <button type="button" onClick={apply} disabled={busy !== null || edited || !proposal.yaml_text || mutationsBlocked} className="rounded border border-emerald-700 px-3 py-1 text-sm text-emerald-200 hover:bg-emerald-950 disabled:opacity-50">
                {busy === "apply" ? "Applying…" : draftId ? "Apply as this draft's YAML" : "Apply as a new draft"}
              </button>
              {edited ? (
                <span className="text-xs text-zinc-500">
                  You edited the proposal: use it in the editor and save, or{" "}
                  <button type="button" onClick={() => setProposalYaml(proposal.yaml_text ?? "")} className="text-cyan-300 underline">
                    reset to the proposal
                  </button>
                  .
                </span>
              ) : (
                <span className="text-xs text-zinc-500">Applying writes the YAML into the draft; approval is a separate action in the editor.</span>
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
