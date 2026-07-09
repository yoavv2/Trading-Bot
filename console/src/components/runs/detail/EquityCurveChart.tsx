"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type EquityPoint = {
  session_date: string;
  total_equity: number;
  cash: number;
  gross_exposure: number;
  realized_pnl: number;
  unrealized_pnl: number;
  open_positions: number;
};

type EquityCurveChartProps = {
  points: EquityPoint[] | null | undefined;
};

/**
 * ANLX-01: renders a minimal Recharts line chart of total_equity over
 * session_date for a backtest run. The `equity_curve` field is only
 * populated for runs the 16-01 backend change has covered (and may be
 * absent/empty for older or in-flight runs), so an empty/null series is
 * NOT a broken-chart bug — it's an explicit, honest "not available" state,
 * never a blank ResponsiveContainer frame.
 */
export function EquityCurveChart({ points }: EquityCurveChartProps) {
  if (!points || points.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        Equity curve not available for this run.
      </p>
    );
  }

  return (
    <div style={{ width: "100%", height: 256 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points}>
          <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
          <XAxis dataKey="session_date" stroke="#a1a1aa" fontSize={11} />
          <YAxis
            stroke="#a1a1aa"
            fontSize={11}
            domain={["auto", "auto"]}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#18181b",
              border: "1px solid #3f3f46",
            }}
            labelStyle={{ color: "#e4e4e7" }}
          />
          <Line
            type="monotone"
            dataKey="total_equity"
            stroke="#22d3ee"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
