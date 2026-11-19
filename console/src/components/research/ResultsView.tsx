"use client";

import { useMemo, useState } from "react";
import type { Candidate, CandidateWindow, Comparison, Metrics } from "@/lib/research/types";
import { GRADE_TONE, STATUS_COPY, VERDICT_TONE, num, pct, tone } from "@/lib/research/present";
import { CurveChart } from "./CurveChart";

const METRIC_ROWS: { label: string; pick: (m: Metrics) => string; group: "return" | "risk" | "trades" }[] = [
  { label: "Net total return", pick: (m) => pct(m.net_total_return), group: "return" },
  { label: "CAGR", pick: (m) => pct(m.cagr), group: "return" },
  { label: "Expectancy", pick: (m) => num(m.expectancy), group: "return" },
  { label: "Max drawdown", pick: (m) => pct(m.max_drawdown), group: "risk" },
  { label: "Drawdown duration (sessions)", pick: (m) => String(m.drawdown_duration), group: "risk" },
  { label: "Sharpe (daily, ann.)", pick: (m) => num(m.sharpe_daily_ann), group: "risk" },
  { label: "Sortino (daily, ann.)", pick: (m) => num(m.sortino_daily_ann), group: "risk" },
  { label: "Exposure", pick: (m) => pct(m.exposure), group: "risk" },
  { label: "Closed trades", pick: (m) => String(m.closed_trades), group: "trades" },
  { label: "Open at end", pick: (m) => String(m.open_at_end.count), group: "trades" },
  { label: "Win rate", pick: (m) => pct(m.win_rate), group: "trades" },
  { label: "Profit factor (report only)", pick: (m) => num(m.profit_factor), group: "trades" },
  { label: "Turnover", pick: (m) => num(m.turnover), group: "trades" },
  { label: "Total costs", pick: (m) => num(m.total_costs.currency), group: "trades" },
  { label: "Rounding slack", pick: (m) => num(m.rounding_slack, 4), group: "trades" },
];

function candidateKey(candidate: Candidate) {
  return `${candidate.strategy_version_id}|${candidate.asset}`;
}

function displayName(candidate: Candidate) {
  return `${candidate.strategy_name ?? "Version"} v${candidate.version_no ?? "?"}`;
}

function evidenceTone(grade: string | null | undefined) {
  return grade === "insufficient" ? "text-warning" : tone(GRADE_TONE, grade);
}

function activeWindow(candidate: Candidate, windows: string[]): { role: string; result: CandidateWindow } | null {
  const role = windows.includes("validation") ? "validation" : windows[0];
  const result = role ? candidate.windows[role] : null;
  return result ? { role, result } : null;
}

function MetricTable({ metrics, benchmark }: { metrics: Metrics; benchmark: Metrics | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-muted-foreground">
          <tr><th className="py-2 font-medium">Metric</th><th className="font-medium">Candidate</th><th className="font-medium">Buy-and-hold</th></tr>
        </thead>
        <tbody>
          {(["return", "risk", "trades"] as const).map((group) => (
            <MetricGroup key={group} group={group} metrics={metrics} benchmark={benchmark} />
          ))}
          {metrics.flags.length > 0 ? <tr className="border-t border-border"><td className="py-2 text-warning">Flags</td><td colSpan={2}>{metrics.flags.join(", ")}</td></tr> : null}
          {Object.keys(metrics.notes).length > 0 ? (
            <tr className="border-t border-border"><td className="py-2 text-muted-foreground">Undefined values</td><td colSpan={2}>
              {Object.entries(metrics.notes).map(([key, note]) => `${key}: ${note}`).join("; ")}
            </td></tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function MetricGroup({ group, metrics, benchmark }: { group: "return" | "risk" | "trades"; metrics: Metrics; benchmark: Metrics | null }) {
  const label = { return: "Return", risk: "Risk", trades: "Trades and costs" }[group];
  return <>
    <tr className="border-t border-border"><td colSpan={3} className="py-2 font-semibold text-foreground">{label}</td></tr>
    {METRIC_ROWS.filter((row) => row.group === group).map((row) => (
      <tr key={row.label} className="border-t border-border/60">
        <td className="py-1.5 text-muted-foreground">{row.label}</td><td>{row.pick(metrics)}</td><td className="text-muted-foreground">{benchmark ? row.pick(benchmark) : "n/a"}</td>
      </tr>
    ))}
  </>;
}

function CandidateDiagnostics({ candidate, revisionId, windows, tie }: { candidate: Candidate; revisionId: string; windows: string[]; tie: boolean }) {
  const [charts, setCharts] = useState(false);
  return (
    <section className="surface-muted rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="font-semibold text-foreground">{displayName(candidate)} <span className="font-mono text-xs text-muted-foreground">{candidate.strategy_version_id.slice(0, 8)}</span> · {candidate.asset}</h4>
        {candidate.rank ? <span className="text-xs text-muted-foreground">Rank {candidate.rank}{tie && candidate.co_leading ? " · co-leading" : ""}</span> : null}
        <button type="button" onClick={() => setCharts((value) => !value)} className="button-secondary ml-auto px-2 py-1 text-xs">
          {charts ? "Hide curves" : "Show curves"}
        </button>
      </div>
      {candidate.status_reasons.length > 0 ? <p className="mt-2 text-xs text-muted-foreground">{candidate.status_reasons.join("; ")}</p> : null}
      <div className="mt-4 grid grid-cols-1 gap-5 xl:grid-cols-2">
        {windows.map((role) => {
          const result = candidate.windows[role];
          const benchmark = candidate.benchmark[role];
          return <div key={role} className="min-w-0">
            <h5 className="eyebrow mb-2">{role.replace(/_/g, " ")} window</h5>
            {result ? <>
              <MetricTable metrics={result.metrics} benchmark={benchmark} />
              <p className="mt-2 text-xs text-muted-foreground">Excess return vs benchmark: {pct(result.excess_return_vs_benchmark ?? null)} (display only; never a gate).</p>
              <p className="mt-2 text-xs">
                Evidence: <span className={evidenceTone(result.evidence.grade)}>{result.evidence.grade.replace(/_/g, " ")}</span>
                {result.evidence.grade_reasons.length ? <span className="text-muted-foreground"> ({result.evidence.grade_reasons.join("; ")})</span> : null}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {result.evidence.descriptors.closed_trades} closed · {result.evidence.descriptors.clusters} clusters · median hold {result.evidence.descriptors.median_holding_sessions ?? "n/a"} sessions · best three {pct(result.evidence.descriptors.concentration.best_three_share)}
              </p>
              {result.evidence.limitations.length > 0 ? <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">{result.evidence.limitations.map((item) => <li key={item}>{item}</li>)}</ul> : null}
              {charts ? <div className="mt-4"><CurveChart revisionId={revisionId} runId={result.run_id} benchmarkRunId={benchmark?.run_id} title={`${candidate.asset} ${role.replace(/_/g, " ")}`} /></div> : null}
            </> : <p className="text-xs text-muted-foreground">No result for this window.</p>}
          </div>;
        })}
      </div>
    </section>
  );
}

/** Compact per-asset result dashboard with full diagnostics preserved below the fold. */
export function ResultsView({ comparison }: { comparison: Comparison }) {
  const assets = useMemo(() => [...new Set(comparison.candidates.map((candidate) => candidate.asset))], [comparison.candidates]);
  const [asset, setAsset] = useState(assets[0] ?? "");
  const [selectedKey, setSelectedKey] = useState(() => comparison.candidates[0] ? candidateKey(comparison.candidates[0]) : "");
  const [group, setGroup] = useState<"ranking" | "asset" | "version">("ranking");
  const tie = comparison.ranking.co_leaders.length > 1;
  const assetCandidates = comparison.candidates.filter((candidate) => candidate.asset === asset);
  const selected = assetCandidates.find((candidate) => candidateKey(candidate) === selectedKey)
    ?? [...assetCandidates].sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999))[0]
    ?? comparison.candidates[0];
  const selectedResult = selected ? activeWindow(selected, comparison.windows_evaluated) : null;
  const selectedBenchmark = selected && selectedResult ? selected.benchmark[selectedResult.role] : null;
  const candidates = [...comparison.candidates];
  if (group === "asset") candidates.sort((a, b) => a.asset.localeCompare(b.asset) || (a.rank ?? 999) - (b.rank ?? 999));
  else if (group === "version") candidates.sort((a, b) => a.strategy_version_id.localeCompare(b.strategy_version_id) || (a.rank ?? 999) - (b.rank ?? 999));
  else candidates.sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999) || a.asset.localeCompare(b.asset));

  return (
    <div className="space-y-4">
      <section className="surface-panel p-4 sm:p-5" style={{ padding: "16px" }} aria-label="Research results">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Initial evaluation</p>
            <h2 className="mt-0.5 text-lg font-semibold text-foreground">Research results</h2>
          </div>
          <span className={`badge ${tone(VERDICT_TONE, comparison.verdict)}`}>{comparison.verdict === "insufficient_evidence" ? "Insufficient evidence" : comparison.verdict === "no_candidate_qualifies" ? <>Constraint not met<span className="sr-only">{comparison.verdict_label}</span></> : comparison.verdict_label}</span>
        </div>

        {comparison.verdict === "insufficient_evidence" ? (
          <p className="mt-2 text-xs text-warning">No candidate can be selected for the final test until the evidence requirement is met.</p>
        ) : null}
        {tie ? <p className="surface-muted mt-4 rounded-md px-3 py-2 text-sm text-warning">{comparison.ranking.co_leaders.length} co-leading candidates tie on the ranking key. A recorded reason is required when freezing one.</p> : null}

        {assets.length > 0 ? <div className="mt-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="eyebrow mb-2">Asset · independent test</p>
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Select asset">
              {assets.map((ticker) => <button key={ticker} type="button" role="tab" aria-selected={ticker === asset} onClick={() => {
                setAsset(ticker);
                const best = comparison.candidates.filter((candidate) => candidate.asset === ticker).sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999))[0];
                if (best) setSelectedKey(candidateKey(best));
              }} className={`rounded-md border px-3 py-1.5 text-sm ${ticker === asset ? "border-accent/30 bg-[var(--accent-soft)] font-semibold text-accent" : "border-transparent text-muted-foreground hover:bg-[var(--surface-muted)]"}`}>{ticker}</button>)}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{selectedResult ? `${selectedResult.role.replace(/_/g, " ")} · ${comparison.windows[selectedResult.role]?.start ?? ""} – ${comparison.windows[selectedResult.role]?.end ?? ""}` : "No measured window"}</p>
        </div> : null}

        {selected && selectedResult ? <>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold text-foreground">{displayName(selected)}</span>
            {selected.rank ? <span className="text-xs text-muted-foreground">Rank {selected.rank}{tie && selected.co_leading ? " · co-leading" : ""}</span> : null}
          </div>
          <section className="metric-grid mt-4" aria-label="Selected candidate metrics">
            <MetricCard label="Net return" value={pct(selectedResult.result.metrics.net_total_return)} context={`Buy-and-hold: ${pct(selectedBenchmark?.net_total_return)}`} toneClass={selectedResult.result.metrics.net_total_return !== null && selectedResult.result.metrics.net_total_return >= 0 ? "text-positive" : "text-negative"} />
            <MetricCard label="Maximum drawdown" value={pct(selectedResult.result.metrics.max_drawdown)} context={`${selectedResult.result.metrics.drawdown_duration} sessions from peak`} toneClass="text-negative" />
            <MetricCard label="Closed trades" value={String(selectedResult.result.metrics.closed_trades)} context={`${pct(selectedResult.result.metrics.exposure)} time in market`} />
            <MetricCard label="Evidence quality" value={selectedResult.result.evidence.grade.replace(/_/g, " ")} context={selectedResult.result.evidence.grade_reasons.join("; ") || `${selectedResult.result.evidence.descriptors.clusters} independent clusters`} toneClass={evidenceTone(selectedResult.result.evidence.grade)} />
          </section>
          {selected.status === "insufficient_evidence" ? <p className="mt-3 text-sm text-warning">This candidate does not have enough evidence to identify a leading candidate or run the final test.</p> : null}
          <section className="surface-muted mt-3 rounded-lg p-3 sm:p-4" aria-label="Strategy and benchmark curves">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div><h3 className="text-sm font-semibold text-foreground">Growth of {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(comparison.settings.initial_capital))}</h3><p className="text-xs text-muted-foreground">{displayName(selected)} · {selected.asset} · {selectedResult.role.replace(/_/g, " ")}</p></div>
              <div className="flex gap-3 text-xs text-muted-foreground"><span><i className="mr-1 inline-block h-0.5 w-3 align-middle bg-accent" />Strategy</span><span><i className="mr-1 inline-block h-0.5 w-3 align-middle bg-muted-foreground" />Buy-and-hold</span></div>
            </div>
            <CurveChart key={`${comparison.revision.revision_id}:${selectedResult.result.run_id}`} revisionId={comparison.revision.revision_id} runId={selectedResult.result.run_id} benchmarkRunId={selectedBenchmark?.run_id} title={`${selected.asset} ${selectedResult.role.replace(/_/g, " ")}`} />
            <p className="mt-1 text-[11px] text-muted-foreground">{comparison.mode_label}</p>
          </section>
        </> : <p className="mt-4 text-sm text-muted-foreground">No candidate result is available for this asset.</p>}
      </section>

      <section className="surface-panel overflow-hidden" aria-label="Candidate comparison">
        <div className="flex flex-wrap items-end justify-between gap-2 p-4 sm:px-5">
          <div><h3 className="font-semibold text-foreground">Compare strategies <span className="badge ml-1">{assetCandidates.length}</span></h3><p className="mt-1 text-xs text-muted-foreground">Select a row to inspect its performance for {asset || "this asset"}.</p></div>
          <span className="eyebrow">{selectedResult?.role.replace(/_/g, " ") ?? "Validation"}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="surface-muted text-muted-foreground"><tr><th className="px-4 py-2 font-medium">Strategy</th><th className="px-4 py-2 font-medium">Net return</th><th className="px-4 py-2 font-medium">Max drawdown</th><th className="px-4 py-2 font-medium">Trades</th><th className="px-4 py-2 font-medium">Evidence</th></tr></thead>
            <tbody>{assetCandidates.map((candidate) => {
              const result = activeWindow(candidate, comparison.windows_evaluated);
              const isSelected = selected && candidateKey(candidate) === candidateKey(selected);
              return <tr key={candidateKey(candidate)} aria-selected={Boolean(isSelected)} className={`border-t border-border ${isSelected ? "bg-accent/5" : ""}`}>
                <td className="px-4 py-2"><button type="button" aria-pressed={Boolean(isSelected)} onClick={() => setSelectedKey(candidateKey(candidate))} className="text-left font-medium text-foreground hover:text-accent"><span>{displayName(candidate)}</span><span className="mt-0.5 block text-[11px] text-muted-foreground">{STATUS_COPY[candidate.status] ?? candidate.status}</span></button></td>
                <td className="px-4 py-2 tabular-nums">{result ? pct(result.result.metrics.net_total_return) : "n/a"}</td>
                <td className="px-4 py-2 tabular-nums">{result ? pct(result.result.metrics.max_drawdown) : "n/a"}</td>
                <td className="px-4 py-2 tabular-nums">{result?.result.metrics.closed_trades ?? "n/a"}</td>
                <td className="px-4 py-2"><span className={evidenceTone(result?.result.evidence.grade)}>{result?.result.evidence.grade.replace(/_/g, " ") ?? "n/a"}</span></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <div className="surface-muted flex flex-wrap justify-between gap-2 px-4 py-3 text-xs text-muted-foreground"><span>{selected?.status === "insufficient_evidence" ? "Insufficient evidence to select this candidate for the final test." : selected ? `Status: ${STATUS_COPY[selected.status] ?? selected.status}` : "No candidate selected."}</span><span>Individual asset results</span></div>
      </section>

      <details className="surface-panel group rounded-lg">
        <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-foreground marker:hidden">
          <span className="mr-2 text-muted-foreground group-open:rotate-90">›</span>Advanced diagnostics, ranking and study details
          <span className="ml-2 text-xs font-normal text-muted-foreground">{comparison.windows_evaluated.join(" · ")}</span>
        </summary>
        <div className="space-y-4 border-t border-border p-4">
          <section className="surface-muted rounded-lg p-4">
            <h3 className="text-sm font-semibold text-foreground">Ranking rationale</h3>
            <p className="mt-1 text-xs text-muted-foreground">Objective {comparison.ranking.objective.replace(/_/g, " ")}; constraint value {comparison.ranking.constraint_value}; key: {comparison.ranking.ranking_key}. Profit factor affects the order: {String(comparison.ranking.profit_factor_affects_order)}.</p>
            {comparison.ranking.ranking.length > 0 ? <div className="mt-3 overflow-x-auto"><table className="w-full text-xs">
              <thead className="text-left text-muted-foreground"><tr><th className="py-2">Rank</th><th>Version</th><th>Asset</th><th>Net return</th><th>Max drawdown</th><th>CAGR</th><th>Excess vs B&amp;H</th><th>Grade</th></tr></thead>
              <tbody>{comparison.ranking.ranking.map((row) => {
                const identity = comparison.candidates.find((candidate) => candidate.strategy_version_id === row.strategy_version_id && candidate.asset === row.asset);
                return <tr key={`${row.strategy_version_id}-${row.asset}`} className="border-t border-border"><td className="py-2">{row.rank}{tie && row.co_leading ? " ★" : ""}</td><td>{identity ? displayName(identity) : "Version"} <span className="font-mono text-muted-foreground">{row.strategy_version_id.slice(0, 8)}</span></td><td>{row.asset}</td><td>{pct(row.net_total_return)}</td><td>{pct(row.max_drawdown)}</td><td>{pct(row.cagr)}</td><td>{pct(row.excess_return_vs_benchmark)}</td><td className={evidenceTone(row.grade)}>{row.grade.replace(/_/g, " ")}</td></tr>;
              })}</tbody>
            </table></div> : <p className="mt-2 text-xs text-muted-foreground">No eligible candidate: nothing is ranked.</p>}
            {tie ? <p className="mt-2 text-xs text-warning">★ {comparison.ranking.co_leaders.length} co-leading candidates tie on the key; freezing one needs a recorded reason.</p> : null}
            <p className="mt-2 text-xs text-muted-foreground">{comparison.benchmark_note}</p>
          </section>

          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">Group candidate detail by:
            {(["ranking", "asset", "version"] as const).map((option) => <button key={option} type="button" onClick={() => setGroup(option)} aria-pressed={group === option} className={`button-secondary px-2 py-1 ${group === option ? "text-accent" : ""}`}>{option}</button>)}
          </div>
          <div className="space-y-3">{candidates.map((candidate) => <CandidateDiagnostics key={candidateKey(candidate)} candidate={candidate} revisionId={comparison.revision.revision_id} windows={comparison.windows_evaluated} tie={tie} />)}</div>

          <section className="surface-muted rounded-lg p-4">
            <h3 className="text-sm font-semibold text-foreground">Study and data details</h3>
            <p className="mt-1 text-xs text-muted-foreground">Windows evaluated: {comparison.windows_evaluated.join(", ")}. Data: {comparison.data.provider} ({comparison.data.adjusted ? "adjusted" : "raw"}); freeze {comparison.data.data_freeze_id?.slice(0, 8) ?? "none"}; input digest {comparison.data.input_digest?.slice(0, 12) ?? "n/a"}; code {comparison.code_sha.slice(0, 12)}.</p>
            <p className="mt-1 text-xs text-muted-foreground">Costs: slippage {comparison.settings.costs?.slippage_bps ?? "not entered"} bps, commission {comparison.settings.costs?.commission_per_order ?? "not entered"}; {comparison.settings.quantity_policy} quantities; capital {comparison.settings.initial_capital}.</p>
            <h4 className="eyebrow mt-4">Limitations</h4>
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">{[...comparison.limitations, ...comparison.evidence_limitations].map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        </div>
      </details>
    </div>
  );
}

function MetricCard({ label, value, context, toneClass = "" }: { label: string; value: string; context: string; toneClass?: string }) {
  return <div className="metric-card"><p className="text-xs text-muted-foreground">{label}</p><p className={`mt-2 truncate text-2xl font-semibold tabular-nums ${toneClass}`} title={value}>{value}</p><p className="mt-1 truncate text-xs text-muted-foreground" title={context}>{context || "No additional evidence detail"}</p></div>;
}
