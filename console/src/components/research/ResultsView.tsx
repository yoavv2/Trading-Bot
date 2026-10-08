"use client";

import { useState } from "react";
import type { Candidate, Comparison, Metrics } from "@/lib/research/types";
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

function MetricsTable({ metrics, benchmark }: { metrics: Metrics; benchmark: Metrics | null }) {
  return (
    <div className="overflow-x-auto">
    <table className="w-full text-xs">
      <thead className="text-left uppercase text-zinc-500">
        <tr>
          <th className="py-1">Metric</th>
          <th>Candidate</th>
          <th>Buy-and-hold</th>
        </tr>
      </thead>
      <tbody>
        {(["return", "risk", "trades"] as const).map((group) => (
          <GroupRows key={group} group={group} metrics={metrics} benchmark={benchmark} />
        ))}
        {metrics.flags.length > 0 ? (
          <tr className="border-t border-zinc-800">
            <td className="py-1 text-amber-300">Flags</td>
            <td colSpan={2} className="text-amber-200">{metrics.flags.join(", ")}</td>
          </tr>
        ) : null}
        {Object.keys(metrics.notes).length > 0 ? (
          <tr className="border-t border-zinc-800">
            <td className="py-1 text-zinc-500">Undefined values</td>
            <td colSpan={2} className="text-zinc-400">
              {Object.entries(metrics.notes)
                .map(([key, note]) => `${key}: ${note}`)
                .join("; ")}
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
    </div>
  );
}

function GroupRows({ group, metrics, benchmark }: { group: "return" | "risk" | "trades"; metrics: Metrics; benchmark: Metrics | null }) {
  const label = { return: "Return", risk: "Risk", trades: "Trades and costs" }[group];
  return (
    <>
      <tr className="border-t border-zinc-800">
        <td colSpan={3} className="py-1 font-semibold text-zinc-300">{label}</td>
      </tr>
      {METRIC_ROWS.filter((row) => row.group === group).map((row) => (
        <tr key={row.label} className="border-t border-zinc-900">
          <td className="py-0.5 text-zinc-400">{row.label}</td>
          <td className="text-zinc-100">{row.pick(metrics)}</td>
          <td className="text-zinc-400">{benchmark ? row.pick(benchmark) : "n/a"}</td>
        </tr>
      ))}
    </>
  );
}

function CandidateCard({ candidate, revisionId, windows, tie }: { candidate: Candidate; revisionId: string; windows: string[]; tie: boolean }) {
  const [charts, setCharts] = useState(false);
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-zinc-100">
          {candidate.strategy_name ?? "version"} v{candidate.version_no ?? "?"} <span className="font-mono text-xs text-zinc-500">{candidate.strategy_version_id.slice(0, 8)}</span> on <span className="font-mono">{candidate.asset}</span>
        </h4>
        <span className={`rounded border border-zinc-700 px-1 text-xs ${candidate.status === "eligible" ? "text-emerald-200" : "text-zinc-300"}`}>
          {STATUS_COPY[candidate.status] ?? candidate.status}
        </span>
        {candidate.rank ? <span className="text-xs text-zinc-400">rank {candidate.rank}{tie && candidate.co_leading ? " (co-leading)" : ""}</span> : null}
        <button type="button" onClick={() => setCharts((c) => !c)} className="ml-auto text-xs text-cyan-300 hover:underline">
          {charts ? "Hide charts" : "Show charts"}
        </button>
      </div>
      {candidate.status_reasons.length > 0 ? <p className="mt-1 text-xs text-zinc-400">{candidate.status_reasons.join("; ")}</p> : null}
      <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
        {windows.map((role) => {
          const window = candidate.windows[role];
          const benchmark = candidate.benchmark[role];
          return (
            <div key={role}>
              <p className="text-xs font-semibold uppercase text-zinc-400">{role} window</p>
              {window ? (
                <>
                  <MetricsTable metrics={window.metrics} benchmark={benchmark} />
                  <p className="mt-1 text-xs text-zinc-400">
                    Excess return vs benchmark: {pct(window.excess_return_vs_benchmark ?? null)} (display only; never a gate).
                  </p>
                  <p className="mt-1 text-xs">
                    Evidence: <span className={tone(GRADE_TONE, window.evidence.grade)}>{window.evidence.grade.replace(/_/g, " ")}</span>
                    {window.evidence.grade_reasons.length ? <span className="text-zinc-400"> ({window.evidence.grade_reasons.join("; ")})</span> : null}
                    <span className="text-zinc-500">
                      {" "}· {window.evidence.descriptors.closed_trades} closed, {window.evidence.descriptors.clusters} clusters, median hold {window.evidence.descriptors.median_holding_sessions ?? "n/a"},
                      best three {pct(window.evidence.descriptors.concentration.best_three_share)}
                    </span>
                  </p>
                  {charts ? <CurveChart revisionId={revisionId} runId={window.run_id} title={`${candidate.asset} ${role}: equity and drawdown`} /> : null}
                </>
              ) : (
                <p className="text-xs text-zinc-500">No result for this window.</p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/**
 * The initial evaluation of a revision from comparison.json: verdict, ranking with its
 * objective and constraint, per-asset candidate cards with return and risk kept in their
 * own groups, the benchmark beside each candidate (display only), evidence grades with
 * their limitations, and the study limitations. Nothing here re-ranks or hides a row.
 */
export function ResultsView({ comparison }: { comparison: Comparison }) {
  const [group, setGroup] = useState<"ranking" | "asset" | "version">("ranking");
  // The backend flags every candidate on the leading key as co_leading, the sole leader
  // included; the tie marker is shown only when more than one candidate shares that key.
  const tie = comparison.ranking.co_leaders.length > 1;
  const candidates = [...comparison.candidates];
  if (group === "asset") {
    candidates.sort((a, b) => a.asset.localeCompare(b.asset) || (a.rank ?? 999) - (b.rank ?? 999));
  } else if (group === "version") {
    candidates.sort((a, b) => a.strategy_version_id.localeCompare(b.strategy_version_id) || (a.rank ?? 999) - (b.rank ?? 999));
  } else {
    candidates.sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999) || a.asset.localeCompare(b.asset));
  }
  return (
    <div className="space-y-4">
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="text-sm font-semibold text-zinc-200">
          Verdict: <span className={tone(VERDICT_TONE, comparison.verdict)}>{comparison.verdict_label}</span>
          <span className="ml-2 rounded border border-zinc-700 px-1 text-xs text-zinc-400">{comparison.mode_label}</span>
        </h3>
        <p className="mt-1 text-xs text-zinc-400">
          Objective {comparison.ranking.objective.replace(/_/g, " ")}, constraint value {comparison.ranking.constraint_value}. Key: {comparison.ranking.ranking_key}. Windows evaluated:{" "}
          {comparison.windows_evaluated.join(", ")}. Profit factor affects the order: {String(comparison.ranking.profit_factor_affects_order)}.
        </p>
        <p className="mt-1 text-xs text-zinc-500">
          Data: {comparison.data.provider} ({comparison.data.adjusted ? "adjusted" : "raw"}); freeze {comparison.data.data_freeze_id?.slice(0, 8) ?? "none"}; input digest{" "}
          {comparison.data.input_digest?.slice(0, 12) ?? "n/a"}; code {comparison.code_sha.slice(0, 12)}. Costs: slippage {comparison.settings.costs?.slippage_bps} bps, commission{" "}
          {comparison.settings.costs?.commission_per_order}; {comparison.settings.quantity_policy} quantities; capital {comparison.settings.initial_capital}.
        </p>
        {comparison.ranking.ranking.length > 0 ? (
          <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="py-1">Rank</th>
                <th>Version</th>
                <th>Asset</th>
                <th>Net return</th>
                <th>Max drawdown</th>
                <th>CAGR</th>
                <th>Excess vs B&amp;H</th>
                <th>Grade</th>
              </tr>
            </thead>
            <tbody>
              {comparison.ranking.ranking.map((row) => {
                const identity = comparison.candidates.find((c) => c.strategy_version_id === row.strategy_version_id);
                return (
                <tr key={`${row.strategy_version_id}-${row.asset}`} className="border-t border-zinc-800">
                  <td className="py-1">{row.rank}{tie && row.co_leading ? " ★" : ""}</td>
                  <td>
                    {identity?.strategy_name ?? "version"} v{identity?.version_no ?? "?"} <span className="font-mono text-xs text-zinc-500">{row.strategy_version_id.slice(0, 8)}</span>
                  </td>
                  <td className="font-mono">{row.asset}</td>
                  <td>{pct(row.net_total_return)}</td>
                  <td>{pct(row.max_drawdown)}</td>
                  <td>{pct(row.cagr)}</td>
                  <td className="text-zinc-400">{pct(row.excess_return_vs_benchmark)}</td>
                  <td className={tone(GRADE_TONE, row.grade)}>{row.grade.replace(/_/g, " ")}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-zinc-400">No eligible candidate: nothing is ranked.</p>
        )}
        {comparison.ranking.co_leaders.length > 1 ? (
          <p className="mt-2 text-xs text-amber-200">★ {comparison.ranking.co_leaders.length} co-leading candidates tie on the key; freezing one needs a recorded reason.</p>
        ) : null}
        <p className="mt-2 text-xs text-zinc-500">{comparison.benchmark_note}</p>
      </section>
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        Group by:
        {(["ranking", "asset", "version"] as const).map((option) => (
          <button key={option} type="button" onClick={() => setGroup(option)} className={`rounded border px-2 py-0.5 ${group === option ? "border-cyan-700 text-cyan-200" : "border-zinc-700 text-zinc-400"}`}>
            {option}
          </button>
        ))}
      </div>
      {candidates.map((candidate) => (
        <CandidateCard key={`${candidate.strategy_version_id}-${candidate.asset}`} candidate={candidate} revisionId={comparison.revision.revision_id} windows={comparison.windows_evaluated} tie={tie} />
      ))}
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4 text-xs text-zinc-400">
        <h3 className="text-sm font-semibold text-zinc-200">Limitations</h3>
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          {comparison.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
          {comparison.evidence_limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
