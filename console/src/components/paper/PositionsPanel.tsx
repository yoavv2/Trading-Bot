"use client";

import { useState } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";

// Types verified against OperatorReadService.list_positions
// (src/trading_platform/services/operator_reads.py:273) and the shared
// collection envelope (build_collection_response,
// src/trading_platform/api/dependencies.py:81).
export type PositionItem = {
  position_id: string;
  strategy_id: string;
  symbol: string;
  status: string; // "open" | "closed" — PAPR-01 "current" = open
  quantity: number;
  average_entry_price: number;
  cost_basis: number;
  opened_session_date: string | null;
  closed_session_date: string | null;
  opened_at: string | null;
  closed_at: string | null;
};

type CollectionResponse<T> = {
  filters: Record<string, unknown>;
  count: number;
  items: T[];
};

const ENDPOINT =
  "/api/v1/operations/positions?strategy_id=trend_following_daily&limit=100";

/**
 * PAPR-01: current (open) positions panel. Self-fetches
 * /api/v1/operations/positions. Neither the RUN-status `status` query
 * param nor any other server-side filter narrows to "open position", so
 * this panel filters client-side to `status === "open"` and discloses any
 * hidden non-open (closed) rows rather than silently dropping them, per
 * the plan's honesty constraint.
 */
export function PositionsPanel() {
  const { loading, result, refetch } =
    useApiQuery<CollectionResponse<PositionItem>>(ENDPOINT);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Positions</h2>
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !result.ok ? (
          <ErrorState failure={result} title="Failed to load positions" />
        ) : (
          <PositionsContent data={result.data} />
        )}
      </div>
    </section>
  );
}

function PositionsContent({ data }: { data: CollectionResponse<PositionItem> }) {
  const [revealed, setRevealed] = useState(false);
  const items = data.items;
  const open = items.filter((p) => p.status === "open");
  const hiddenCount = items.length - open.length;
  const capped = data.count >= 100;

  const rows = revealed ? items : open;

  return (
    <div className="space-y-3">
      {capped ? (
        <p className="text-xs text-amber-300">
          Showing the 100 most-recent positions; older rows may be truncated.
        </p>
      ) : null}

      {hiddenCount > 0 ? (
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <span>
            {revealed
              ? `Showing all ${items.length} position(s), including ${hiddenCount} non-open.`
              : `Hiding ${hiddenCount} non-open position(s).`}
          </span>
          <button
            type="button"
            onClick={() => setRevealed((r) => !r)}
            className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300 hover:bg-zinc-800"
          >
            {revealed ? "Show open only" : "Reveal all"}
          </button>
        </div>
      ) : null}

      {rows.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {open.length === 0 && !capped
            ? "No open positions."
            : open.length === 0 && capped
              ? "No open positions among the 100 most-recent; older rows may be truncated."
              : "No positions to show."}
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-zinc-800 text-zinc-500">
              <th className="py-1 pr-2 font-normal">Symbol</th>
              <th className="py-1 pr-2 font-normal">Quantity</th>
              <th className="py-1 pr-2 font-normal">Avg entry price</th>
              <th className="py-1 pr-2 font-normal">Cost basis</th>
              <th className="py-1 pr-2 font-normal">Opened</th>
              {revealed ? (
                <th className="py-1 font-normal">Status</th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr
                key={p.position_id}
                className={
                  revealed && p.status !== "open"
                    ? "border-t border-zinc-800 text-zinc-500"
                    : "border-t border-zinc-800 text-zinc-200"
                }
              >
                <td className="py-1 pr-2">{p.symbol}</td>
                <td className="py-1 pr-2">{p.quantity}</td>
                <td className="py-1 pr-2">{p.average_entry_price}</td>
                <td className="py-1 pr-2">{p.cost_basis}</td>
                <td className="py-1 pr-2">
                  {p.opened_session_date ?? p.opened_at ?? "—"}
                </td>
                {revealed ? <td className="py-1">{p.status}</td> : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
