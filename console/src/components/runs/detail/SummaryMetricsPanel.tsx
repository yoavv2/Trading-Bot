"use client";

type SummaryMetricsPanelProps = {
  metrics: Record<string, unknown> | null | undefined;
};

const METRIC_FIELDS: Array<{ key: string; label: string }> = [
  { key: "sharpe_ratio", label: "Sharpe" },
  { key: "max_drawdown_pct", label: "Max drawdown %" },
  { key: "win_rate_pct", label: "Win rate %" },
  { key: "total_return_pct", label: "Total return %" },
  { key: "trade_count", label: "Trade count" },
];

function formatValue(value: unknown): string {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value.toLocaleString() : "—";
  }
  return "—";
}

/**
 * ANLX-02: labeled headline summary-metrics panel (Sharpe, max drawdown,
 * win rate, total return %, trade count). Reads verbatim from
 * `backtest.metrics` — no derived arithmetic, no recomputation, per
 * 16-CONTEXT. A missing/absent metrics object (or one containing none of
 * the five headline keys) renders an explicit "not available" state rather
 * than a panel of dashes; an individual missing key within an otherwise
 * present metrics object renders "—" for that field only.
 */
export function SummaryMetricsPanel({ metrics }: SummaryMetricsPanelProps) {
  const hasAnyField =
    !!metrics &&
    METRIC_FIELDS.some((field) => metrics[field.key] !== undefined);

  if (!hasAnyField) {
    return (
      <p className="text-sm text-zinc-500">
        Summary metrics not available for this run.
      </p>
    );
  }

  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-5">
      {METRIC_FIELDS.map((field) => (
        <div key={field.key}>
          <dt className="text-xs uppercase text-zinc-500">{field.label}</dt>
          <dd className="text-zinc-200">{formatValue(metrics?.[field.key])}</dd>
        </div>
      ))}
    </dl>
  );
}
