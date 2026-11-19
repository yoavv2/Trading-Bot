"use client";

import { useEffect, useRef, useState } from "react";
import { requestStudyProposal } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import type { MutationCapability } from "@/lib/useMutationCapability";
import type { AssistantStatus, StudyProposal } from "@/lib/research/types";
import { planApplication } from "@/lib/research/proposal";
import type { WizardState } from "./StudyWizard";
import { WindowTimeline } from "./ResearchPeriod";

const BOX = "surface-panel";
const FIELD = "mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-sm text-zinc-100";
const EXAMPLE = "Compare the approved trend-following strategies on AAPL and MSFT from 2018 through 2025.";

type Props = {
  current: WizardState;
  isRevision: boolean;
  capability: MutationCapability;
  onApply: (patch: Partial<WizardState>, summary: string) => void;
};

/**
 * Optional "Describe your study" assistant. One request proposes study settings; the
 * proposal is shown for review (what it understood, what is missing, which strategies
 * resolved against approved versions, which fields would be replaced) and can be revised
 * with follow-up instructions. "Apply to form" only patches the wizard; creating the study
 * stays the wizard's own button. Without the assistant the form is fully usable.
 */
export function StudyAssistant({ current, isRevision, capability, onApply }: Props) {
  const status = useApiQuery<{ assistant: AssistantStatus }>("/api/v1/research/assistant");
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [proposal, setProposal] = useState<StudyProposal | null>(null);
  // Follow-ups sent for the current proposal on THIS page load: a display counter only. The
  // server enforces the daily allowance and concurrency, not a per-proposal cap.
  const [followUps, setFollowUps] = useState(0);
  const [busy, setBusy] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [error, setError] = useState<string | null>(null);
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
  const mutationsBlocked = capability.state !== "enabled";
  const usable = Boolean(assistant?.configured) && !exhausted && !mutationsBlocked && !busy;

  async function ask(userText: string, base: StudyProposal | null) {
    if (!usable || inFlight.current || !userText.trim()) {
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setStartedAt(Date.now());
    setNow(Date.now());
    setError(null);
    const result = await requestStudyProposal({
      user_text: userText.trim(),
      // The whole reviewable context goes back, outstanding unsupported requests included,
      // so an unrelated follow-up cannot silently drop them.
      base_proposal: base ? { summary: base.summary, missing: base.missing, unsupported_requests: base.unsupported_requests, settings: base.settings } : null,
    });
    inFlight.current = false;
    setBusy(false);
    setStartedAt(null);
    status.refetch();
    if (!result.ok) {
      setError(result.message);
      return;
    }
    setProposal((result.data as { proposal: StudyProposal }).proposal);
    setFollowUps(base ? followUps + 1 : 0);
    setFollowUp("");
  }

  // One calculation feeds the preview, the change table and what "Apply to form" patches.
  const application = proposal ? planApplication(proposal, current, isRevision) : null;
  const s = proposal?.settings ?? null;
  const periodTouched = application ? application.changes.some((c) => ["rangeStart", "rangeEnd", "periodMode", "split", "development", "validation", "finalTest"].includes(c.key)) : false;

  return (
    <section aria-label="Study assistant" className={`${BOX} space-y-3`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-200">Plan with the AI assistant</h2>
        <div className="flex items-center gap-3">
          {assistant ? (
            <span className="text-xs text-zinc-500">
              {assistant.provider}/{assistant.model} · today {assistant.usage.requests_today}/{assistant.limits.max_requests_per_day} requests
            </span>
          ) : null}
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="rounded border border-zinc-700 px-2 py-0.5 text-xs text-zinc-200 hover:bg-zinc-800">
            {open ? "Hide" : "Show"}
          </button>
        </div>
      </div>

      {!open ? <p className="text-xs text-zinc-500">Describe your research idea in plain English. The assistant proposes settings for you to review.</p> : null}

      {open ? (
        <>
          {statusFailure ? (
            <p role="alert" className="text-sm text-amber-200">
              Assistant status unavailable ({statusFailure.status ?? "unreachable"}): {statusFailure.message}. The form keeps working.
            </p>
          ) : null}
          {assistant && !assistant.configured ? (
            <div role="status" className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-100">
              <p className="font-semibold">{assistant.enabled ? "The assistant is enabled but not configured." : "The assistant is disabled."}</p>
              <p className="mt-1">The form, readiness and runs work without it. To activate it, set in the API environment and restart:</p>
              <ul className="mt-1 list-disc pl-5 font-mono text-xs">
                {assistant.missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <p className="mt-1 text-xs text-amber-200/80">{assistant.note}</p>
            </div>
          ) : null}
          {exhausted && assistant?.configured ? (
            <p role="status" className="text-sm text-amber-200">
              Today&apos;s allowance is used up ({assistant.usage.requests_today}/{assistant.limits.max_requests_per_day}). The form keeps working.
            </p>
          ) : null}

          <label className="block text-sm">
            <span className="text-zinc-400">What do you want to study?</span>
            <textarea value={text} onChange={(event) => setText(event.target.value)} rows={3} disabled={busy || !assistant?.configured} className={FIELD} placeholder={`e.g. ${EXAMPLE}`} />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => ask(text, null)} disabled={!usable || !text.trim()} className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950 disabled:opacity-50">
              {busy && !proposal ? `Asking the assistant… ${elapsed}s${assistant ? ` of ${assistant.limits.timeout_seconds}s` : ""}` : proposal ? "Start over with a new description" : "Propose settings"}
            </button>
            <button type="button" onClick={() => setText(EXAMPLE)} disabled={busy} className="text-xs text-cyan-300 underline disabled:opacity-50">
              use the example
            </button>
            {mutationsBlocked ? <span className="text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</span> : null}
          </div>
          <p className="text-xs text-zinc-500">Missing details are listed, not invented. Nothing is created until you apply and then create the study.</p>

          {error ? (
            <p role="alert" className="rounded border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-100">
              {error} Your text{proposal ? " and the last proposal are" : " is"} kept.
            </p>
          ) : null}

          {proposal && s && application ? (
            <div className="space-y-3 rounded border border-zinc-700 bg-zinc-950/40 p-3" data-testid="study-proposal">
              <details className="text-xs text-zinc-500">
                <summary className="cursor-pointer">
                  Proposal{followUps > 0 ? ` · follow-up ${followUps} on this page` : ""} · {proposal.provenance.model}
                </summary>
                <p className="mt-1">
                  prompt {proposal.prompt_version} · attempt {proposal.attempt_no} · request {proposal.provenance.request_id ?? "—"} · tokens {proposal.provenance.input_tokens ?? "?"}/{proposal.provenance.output_tokens ?? "?"}. The follow-up count is this page&apos;s only (it resets on reload or a new description); the API enforces the daily allowance and concurrency, every request counted.
                </p>
              </details>
              <div>
                <p className="text-xs uppercase text-zinc-500">What the assistant understood</p>
                <p className="mt-1 text-sm text-zinc-200">{proposal.summary || "—"}</p>
              </div>

              {proposal.missing.length > 0 ? (
                <div className="rounded border border-amber-900 bg-amber-950/30 px-3 py-2">
                  <p className="text-xs uppercase text-amber-300">Missing ({proposal.missing.length}): fill in by hand or tell the assistant</p>
                  <ul className="mt-1 list-disc pl-5 text-sm text-amber-100">
                    {proposal.missing.map((item, index) => (
                      <li key={`${item.field}-${index}`}>
                        <span className="font-mono text-xs">{item.field}</span>: {item.detail}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {s.strategies.unresolved.length > 0 ? (
                <div className="rounded border border-amber-900 bg-amber-950/30 px-3 py-2 text-sm text-amber-100">
                  <p className="text-xs uppercase text-amber-300">Not an approved strategy ({s.strategies.unresolved.length})</p>
                  <ul className="mt-1 list-disc pl-5">
                    {s.strategies.unresolved.map((item, index) => (
                      <li key={`${item.reference}-${index}`}>
                        &quot;{item.reference}&quot;: {item.reason}. Pick the version by hand in section 1.
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {s.strategies.resolved.length > 0 ? (
                <div>
                  <p className="text-xs uppercase text-zinc-500">Approved versions matched</p>
                  <ul className="mt-1 space-y-0.5 text-sm text-zinc-200">
                    {s.strategies.resolved.map((item) => (
                      <li key={item.version_id}>
                        {item.name} v{item.version_no} <span className="font-mono text-xs text-zinc-500">{item.version_id.slice(0, 8)}</span>{" "}
                        <span className="text-xs text-zinc-500">(by {item.matched_by === "name" ? "name" : "id"}, for &quot;{item.reference}&quot;)</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {s.assets.length > 0 ? (
                <div>
                  <p className="text-xs uppercase text-zinc-500">Assets</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {s.assets.map((asset) => (
                      <span key={asset.ticker} className={`rounded border px-1.5 py-0.5 font-mono text-xs ${asset.in_catalog ? "border-cyan-800 bg-cyan-950/40 text-cyan-100" : "border-amber-800 bg-amber-950/40 text-amber-100"}`} title={asset.name ?? undefined}>
                        {asset.ticker}
                        {asset.name ? <span className="ml-1 font-sans text-zinc-400">{asset.name}</span> : null}
                        {!asset.in_catalog ? <span className="ml-1 font-sans text-[10px] uppercase text-amber-300">not in catalog</span> : null}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              {proposal.unsupported_requests.length > 0 ? (
                <div className="text-sm">
                  <p className="text-xs uppercase text-zinc-500">Not expressible as study settings ({proposal.unsupported_requests.length}); kept through follow-ups</p>
                  <ul className="mt-1 list-disc pl-5 text-zinc-300">
                    {proposal.unsupported_requests.map((item, index) => (
                      <li key={`${item.code}-${index}`}>
                        <code className="text-amber-200">{item.code}</code> {item.detail}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {periodTouched ? (
                <div>
                  <p className="text-xs uppercase text-zinc-500">
                    Periods after applying ({application.period.mode === "custom" ? "custom dates" : `preset ${application.period.split.development_pct} / ${application.period.split.validation_pct} / ${application.period.split.final_test_pct}`})
                  </p>
                  <div className="mt-1">
                    <WindowTimeline windows={application.period.windows} />
                  </div>
                </div>
              ) : (
                <p className="text-xs text-zinc-500">Research period unchanged{current.periodMode === "custom" ? " (custom periods kept)" : ""}.</p>
              )}

              <div>
                <p className="text-xs uppercase text-zinc-500">&quot;Apply to form&quot; replaces ({application.changes.length})</p>
                {application.changes.length === 0 ? (
                  <p className="mt-1 text-sm text-zinc-500">Nothing to apply: the proposal filled no field.</p>
                ) : (
                  <div className="mt-1 overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="text-zinc-500">
                        <tr>
                          <th className="py-1 pr-2 font-normal">Field</th>
                          <th className="py-1 pr-2 font-normal">Current</th>
                          <th className="py-1 font-normal">Proposed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {application.changes.map((change) => (
                          <tr key={change.key} className="border-t border-zinc-800 align-top">
                            <td className="py-1 pr-2 text-zinc-300">{change.label}</td>
                            <td className="py-1 pr-2 font-mono text-zinc-500">{change.from}</td>
                            <td className="py-1 font-mono text-zinc-100">{change.to}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="mt-1 text-xs text-zinc-500">Everything else keeps its current value.</p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => onApply(application.patch, `Applied the assistant's proposal: ${application.changes.length} field(s) replaced.`)}
                  disabled={busy || application.changes.length === 0}
                  className="rounded border border-emerald-700 px-3 py-1 text-sm text-emerald-200 hover:bg-emerald-950 disabled:opacity-50"
                >
                  Apply to form
                </button>
                <button type="button" onClick={() => setProposal(null)} disabled={busy} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800 disabled:opacity-50">
                  Discard proposal
                </button>
                <span className="text-xs text-zinc-500">Creating the study stays the button at the bottom.</span>
              </div>

              <label className="block text-sm">
                <span className="text-zinc-400">Revise the proposal (follow-up instruction)</span>
                <textarea value={followUp} onChange={(event) => setFollowUp(event.target.value)} rows={2} disabled={busy || !assistant?.configured} className={FIELD} placeholder="e.g. use 2016 to 2024 instead and add NVDA" />
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={() => ask(followUp, proposal)} disabled={!usable || !followUp.trim()} className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950 disabled:opacity-50">
                  {busy ? `Revising… ${elapsed}s` : "Revise proposal"}
                </button>
                <span className="text-xs text-zinc-500">Each follow-up is one request from the daily allowance.</span>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
