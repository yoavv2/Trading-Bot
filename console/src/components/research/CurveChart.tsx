"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import type { Curve } from "@/lib/research/types";

/** Equity and drawdown of one research run, loaded from its per-run curve endpoint. */
export function CurveChart({
  revisionId,
  runId,
  benchmarkRunId,
  title,
}: {
  revisionId: string;
  runId: string;
  benchmarkRunId?: string | null;
  title: string;
}) {
  const base = `/api/v1/research/revisions/${encodeURIComponent(revisionId)}/runs`;
  const curve = useApiQuery<Curve>(`${base}/${encodeURIComponent(runId)}/curve`);
  const benchmark = useApiQuery<Curve>(
    `${base}/${encodeURIComponent(benchmarkRunId ?? runId)}/curve`,
  );

  if (!curve.result || !benchmark.result) {
    return <p className="text-xs text-muted-foreground">Loading chart…</p>;
  }
  if (!curve.result.ok) {
    return <ErrorState failure={curve.result} title={`${title}: curve unavailable`} />;
  }
  if (benchmarkRunId && !benchmark.result.ok) {
    return <ErrorState failure={benchmark.result} title={`${title}: benchmark curve unavailable`} />;
  }
  const points = curve.result.data.points;
  const benchmarkPoints = benchmarkRunId && benchmark.result.ok ? benchmark.result.data.points : [];
  if (points.length === 0) {
    return <p className="text-xs text-muted-foreground">{title}: no equity series.</p>;
  }
  const benchmarkByDate = new Map(benchmarkPoints.map((point) => [point.session_date, point]));
  const data = points.map((point) => ({
    ...point,
    benchmark_equity: benchmarkByDate.get(point.session_date)?.total_equity ?? null,
    benchmark_drawdown: benchmarkByDate.get(point.session_date)?.drawdown ?? null,
  }));
  const money = (value: number) =>
    new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
  const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-xs font-medium text-foreground">{title} · Equity</p>
          <span className="text-xs text-muted-foreground">USD</span>
        </div>
        <div className="h-64 w-full min-w-0" role="img" aria-label={`${title}: strategy and buy-and-hold equity`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 8, bottom: 2, left: 8 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="session_date" minTickGap={36} tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis width={64} tickFormatter={money} tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
                labelStyle={{ color: "var(--muted)" }}
                formatter={(value) => (typeof value === "number" ? money(value) : "n/a")}
              />
              {benchmarkPoints.length > 0 ? <Legend wrapperStyle={{ fontSize: 11, color: "var(--muted)" }} /> : null}
              <Line type="monotone" dataKey="total_equity" name="Strategy" stroke="var(--accent)" strokeWidth={2.5} dot={false} activeDot={{ r: 3 }} />
              {benchmarkPoints.length > 0 ? <Line type="monotone" dataKey="benchmark_equity" name="Buy and hold" stroke="var(--muted)" strokeWidth={1.75} strokeDasharray="5 4" dot={false} activeDot={{ r: 3 }} connectNulls /> : null}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="min-w-0">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-xs font-medium text-foreground">Drawdown</p>
          <span className="text-xs text-muted-foreground">Peak-to-trough decline</span>
        </div>
        <div className="h-64 w-full min-w-0" role="img" aria-label={`${title}: strategy and buy-and-hold drawdown`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 8, bottom: 2, left: 8 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="session_date" hide />
              <YAxis width={64} tickFormatter={percent} tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} domain={["auto", 0]} />
              <Tooltip
                contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
                labelStyle={{ color: "var(--muted)" }}
                formatter={(value) => (typeof value === "number" ? percent(value) : "n/a")}
              />
              <Line type="monotone" dataKey="drawdown" name="Strategy" stroke="var(--negative)" strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
              {benchmarkPoints.length > 0 ? <Line type="monotone" dataKey="benchmark_drawdown" name="Buy and hold" stroke="var(--muted)" strokeWidth={1.5} strokeDasharray="5 4" dot={false} activeDot={{ r: 3 }} connectNulls /> : null}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
