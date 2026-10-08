"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import type { Curve } from "@/lib/research/types";

/** Equity and drawdown of one research run, loaded from the per-run curve endpoint. */
export function CurveChart({ revisionId, runId, title }: { revisionId: string; runId: string; title: string }) {
  const curve = useApiQuery<Curve>(
    `/api/v1/research/revisions/${encodeURIComponent(revisionId)}/runs/${encodeURIComponent(runId)}/curve`,
  );
  if (!curve.result) {
    return <p className="text-xs text-zinc-500">Loading chart…</p>;
  }
  if (!curve.result.ok) {
    return <ErrorState failure={curve.result} title={`${title}: curve unavailable`} />;
  }
  const points = curve.result.data.points;
  if (points.length === 0) {
    return <p className="text-xs text-zinc-500">{title}: no equity series.</p>;
  }
  return (
    <div>
      <p className="text-xs text-zinc-400">{title}</p>
      <div style={{ width: "100%", height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points}>
            <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
            <XAxis dataKey="session_date" stroke="#a1a1aa" fontSize={10} />
            <YAxis stroke="#a1a1aa" fontSize={10} domain={["auto", "auto"]} />
            <Tooltip contentStyle={{ backgroundColor: "#18181b", border: "1px solid #3f3f46" }} labelStyle={{ color: "#e4e4e7" }} />
            <Line type="monotone" dataKey="total_equity" stroke="#22d3ee" dot={false} name="equity" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div style={{ width: "100%", height: 100 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points}>
            <XAxis dataKey="session_date" hide />
            <YAxis stroke="#a1a1aa" fontSize={10} domain={["auto", 0]} tickFormatter={(value: number) => `${(value * 100).toFixed(0)}%`} />
            <Tooltip contentStyle={{ backgroundColor: "#18181b", border: "1px solid #3f3f46" }} labelStyle={{ color: "#e4e4e7" }} />
            <Line type="monotone" dataKey="drawdown" stroke="#f87171" dot={false} name="drawdown" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
