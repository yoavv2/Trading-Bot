"use client";

import { useState } from "react";
import Link from "next/link";
import { freezeRevision, runFinalTest } from "@/lib/api";
import { useMutationCapability } from "@/lib/useMutationCapability";
import type { Comparison, Exposures, FinalTestBlock, Freeze } from "@/lib/research/types";
import { OUTCOME_TONE, pct, tone } from "@/lib/research/present";
import { CurveChart } from "./CurveChart";

export function ExposuresList({ exposures }: { exposures: Exposures }) {
  return (
    <div className="text-xs">
      <p className="text-zinc-400">
        Recorded final-test exposures of {exposures.assets.join(", ")} overlapping {exposures.proposed_window.start} to {exposures.proposed_window.end}: {exposures.count} (
        {exposures.by_state.run_recorded ?? 0} run recorded, {exposures.by_state.results_inspected ?? 0} results inspected), across every study, revision, family and version.
      </p>
      {exposures.items.length > 0 ? (
        <ul className="mt-1 space-y-1">
          {exposures.items.map((item) => (
            <li key={item.exposure_id} className="rounded border border-zinc-800 px-2 py-1">
              <span className="font-mono">{item.asset}</span> {item.range.start} to {item.range.end} · {item.context.study_name ?? "study"} rev {item.context.revision_no ?? "?"} ·{" "}
              {item.context.strategy_name ?? "version"} v{item.context.version_no ?? "?"} · outcome {item.context.outcome ?? "none"} ·{" "}
              <span className={item.state === "results_inspected" ? "text-amber-200" : "text-zinc-300"}>{item.state.replace(/_/g, " ")}</span>
              {item.reason === "reproducibility_failure" ? <span className="ml-1 text-red-300">reproducibility failure</span> : null}
              {item.context.same_revision ? <span className="ml-1 text-zinc-500">(this revision)</span> : null}
              {item.link && item.revision_id ? (
                <Link href={`/research/studies/${item.study_id}?revision=${item.revision_id}`} className="ml-1 text-cyan-300 hover:underline">
                  open
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="mt-1 text-zinc-500">{exposures.limitation}</p>
    </div>
  );
}

/**
 * Freeze (candidate + acceptance values + co-leading reason), the final-test action, the
 * outcome block with its separate benchmark comparison, reproducibility, and the global
 * exposures. The outcome never changes the ranking above it.
 */
export function FinalTestPanel({
  revisionId,
  comparison,
  freeze,
  finalTest,
  finalTestInFlight = false,
  exposures,
  onChanged,
}: {
  revisionId: string;
  comparison: Comparison | null;
  freeze: Freeze | null;
  finalTest: FinalTestBlock;
  finalTestInFlight?: boolean;
  exposures: Exposures | null;
  onChanged: () => void;
}) {
  const capability = useMutationCapability();
  const [selected, setSelected] = useState<string>("");
  const [constraint, setConstraint] = useState("");
  const [minimum, setMinimum] = useState("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canMutate = capability.state === "enabled" && !busy;
  const eligible = comparison?.candidates.filter((c) => c.status === "eligible") ?? [];
  const coLeading = comparison?.ranking.co_leaders.length ?? 0;

  async function doFreeze() {
    const [versionId, asset] = selected.split("|");
    if (!versionId || !asset) {
      setMessage("Choose a candidate.");
      return;
    }
    setBusy(true);
    setMessage(null);
    const result = await freezeRevision(revisionId, {
      strategy_version_id: versionId,
      asset,
      acceptance: { constraint_value: Number(constraint), objective_minimum: Number(minimum) },
      co_leading_choice_reason: reason.trim() || undefined,
    });
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    onChanged();
  }

  async function doFinalTest(rerun: boolean) {
    setBusy(true);
    setMessage(null);
    const result = await runFinalTest(revisionId, rerun);
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    onChanged();
  }

  return (
    <section className="surface-panel rounded-lg p-4 sm:p-5">
      <p className="eyebrow">Out-of-sample confirmation</p>
      <h3 className="mt-1 text-base font-semibold text-foreground">Final test</h3>
      {!freeze ? (
        <div className="mt-2 space-y-2 text-sm">
          <p className="text-xs text-muted-foreground">
            Freeze the chosen candidate and the acceptance values before anything runs on the final-test window. Only the frozen candidate and its buy-and-hold benchmark will run.
          </p>
          {comparison === null ? (
            <p className="text-xs text-muted-foreground">Available once the initial evaluation exists.</p>
          ) : eligible.length === 0 ? (
            <p className="text-xs text-muted-foreground">No eligible candidate to freeze ({comparison.verdict_label}).</p>
          ) : (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              <label className="block text-xs text-muted-foreground">
                Candidate (eligible only)
                <select value={selected} onChange={(event) => setSelected(event.target.value)} className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground">
                  <option value="">choose…</option>
                  {eligible.map((c) => (
                    <option key={`${c.strategy_version_id}|${c.asset}`} value={`${c.strategy_version_id}|${c.asset}`}>
                      rank {c.rank}{coLeading > 1 && c.co_leading ? " ★" : ""}: {c.strategy_name} v{c.version_no} on {c.asset}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs text-muted-foreground">
                Acceptance: constraint value on the test window ({comparison.ranking.objective === "risk_first" ? "CAGR floor" : "max drawdown cap"})
                <input value={constraint} onChange={(event) => setConstraint(event.target.value)} className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" placeholder="required" />
              </label>
              <label className="block text-xs text-muted-foreground">
                Acceptance: minimum for the objective dimension ({comparison.ranking.objective === "risk_first" ? "max drawdown, e.g. -0.15" : "net return, e.g. 0.05"})
                <input value={minimum} onChange={(event) => setMinimum(event.target.value)} className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" placeholder="required" />
              </label>
              <label className="block text-xs text-muted-foreground">
                Co-leading choice reason {coLeading > 1 ? "(required: the ranking has a tie)" : "(optional)"}
                <input value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" />
              </label>
              <div className="md:col-span-2">
                <button type="button" onClick={doFreeze} disabled={!canMutate || !selected || constraint.trim() === "" || minimum.trim() === ""} className="button-primary px-3 py-2 text-sm disabled:opacity-50">
                  Freeze candidate and acceptance criteria
                </button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-2 space-y-2 text-sm">
          <p className="text-xs text-muted-foreground">
            Frozen {freeze.frozen_at?.slice(0, 16)}: candidate <span className="font-mono">{freeze.candidate.strategy_version_id.slice(0, 8)}</span> on <span className="font-mono">{freeze.candidate.asset}</span>; acceptance constraint{" "}
            {freeze.acceptance.constraint_value}, objective minimum {freeze.acceptance.objective_minimum}
            {freeze.co_leading_choice_reason ? `; co-leading choice: ${freeze.co_leading_choice_reason}` : ""}.
          </p>
          {finalTest.state !== "evaluated" ? (
            finalTestInFlight ? (
              <p className="rounded-md bg-warning/10 p-3 text-xs text-warning">Final test running: the frozen candidate and its benchmark are under system activity below; the outcome appears here when the evaluation completes.</p>
            ) : (
              <button type="button" onClick={() => doFinalTest(false)} disabled={!canMutate} className="button-primary px-3 py-2 text-sm disabled:opacity-50">
                Run the final test (frozen candidate and its benchmark only)
              </button>
            )
          ) : (
            <div className="space-y-2">
              <p>
                Outcome: <span className={`font-semibold ${tone(OUTCOME_TONE, finalTest.outcome)}`}>{finalTest.outcome?.replace(/_/g, " ")}</span>
                {finalTest.thin_evidence ? <span className="ml-2 badge text-warning">thin evidence</span> : null}
                {finalTest.is_rerun ? <span className="ml-2 text-xs text-muted-foreground">(technical rerun)</span> : null}
              </p>
              {finalTest.outcome_reasons?.length ? <ul className="list-disc pl-4 text-xs text-foreground">{finalTest.outcome_reasons.map((r) => <li key={r}>{r}</li>)}</ul> : null}
              <p className="text-xs text-muted-foreground">{finalTest.wording}</p>
              <div className="surface-muted rounded-md p-3 text-xs">
                <p className="font-semibold text-foreground">Benchmark comparison (shown separately; not part of the outcome)</p>
                {finalTest.benchmark_comparison ? (
                  <p className="text-muted-foreground">
                    Return {finalTest.benchmark_comparison.return_vs_benchmark ?? "n/a"} than buy-and-hold, drawdown {finalTest.benchmark_comparison.drawdown_vs_benchmark ?? "n/a"}, excess return{" "}
                    {pct(finalTest.benchmark_comparison.excess_return_vs_benchmark)}. {finalTest.benchmark_comparison.note}
                  </p>
                ) : (
                  <p className="text-muted-foreground">Benchmark not available.</p>
                )}
              </div>
              {finalTest.candidate?.metrics ? (
                <p className="text-xs text-muted-foreground">
                  Test window: net return {pct(finalTest.candidate.metrics.net_total_return)}, max drawdown {pct(finalTest.candidate.metrics.max_drawdown)}, CAGR {pct(finalTest.candidate.metrics.cagr)}, closed trades{" "}
                  {finalTest.candidate.metrics.closed_trades}; grade {finalTest.candidate.evidence?.grade.replace(/_/g, " ")}.
                </p>
              ) : null}
              {finalTest.reproducibility ? (
                <p className={`text-xs ${finalTest.reproducibility.byte_identical ? "text-positive" : "text-negative"}`}>
                  Reproducibility: {finalTest.reproducibility.status.replace(/_/g, " ")} (byte identical: {String(finalTest.reproducibility.byte_identical)})
                </p>
              ) : null}
              <p className="text-xs text-muted-foreground">The initial ranking is never changed by the final test.</p>
              {finalTest.candidate?.run_id ? <CurveChart revisionId={revisionId} runId={finalTest.candidate.run_id} benchmarkRunId={finalTest.benchmark?.run_id} title="Final test" /> : null}
              {finalTestInFlight ? (
                <p className="text-xs text-warning">A technical rerun is in progress.</p>
              ) : (
                <button type="button" onClick={() => doFinalTest(true)} disabled={!canMutate} className="button-secondary px-3 py-2 text-xs disabled:opacity-50">
                  Technical rerun (must reproduce byte for byte)
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {message ? <p role="alert" className="mt-2 text-sm text-negative">{message}</p> : null}
      {capability.state !== "enabled" ? <p className="mt-1 text-xs text-muted-foreground">{capability.reason ?? "Mutation availability unknown"}</p> : null}
      {exposures ? (
        <div className="mt-3 border-t border-border pt-3">
          <ExposuresList exposures={exposures} />
        </div>
      ) : null}
    </section>
  );
}
