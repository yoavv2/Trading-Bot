"use client";

import { useState } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";

// Types verified against OperatorReadService.list_paper_orders
// (src/trading_platform/services/operator_reads.py:175) and the shared
// collection envelope (build_collection_response,
// src/trading_platform/api/dependencies.py:81). Field set trimmed to what
// PAPR-02 needs; full shape also mirrored in
// components/runs/detail/OrdersFillsPanel.tsx.
export type OrderItem = {
  order_id: string;
  run_id: string;
  strategy_id: string;
  symbol: string;
  session_date: string;
  side: string;
  quantity: number;
  order_type: string;
  status: string; // OrderLifecycleState value — see OPEN_STATUSES below
  broker_status: string | null;
  client_order_id: string; // PAPR-02 intent id
  broker_order_id: string | null;
  submitted_at: string | null;
  filled_at: string | null;
  last_submission_error: string | null;
  last_sync_error: string | null;
};

type CollectionResponse<T> = {
  filters: Record<string, unknown>;
  count: number;
  items: T[];
};

// OrderLifecycleState values (src/trading_platform/db/models/order_event.py:19).
// Only these are shown by default; everything else (filled, canceled,
// rejected, expired, submission_failed, unknown) is terminal/ambiguous and
// goes in the revealed bucket.
const OPEN_STATUSES = new Set([
  "pending_submission",
  "submitted",
  "partially_filled",
]);

const ENDPOINT =
  "/api/v1/operations/orders?strategy_id=trend_following_daily&limit=100";

/**
 * PAPR-02: open-orders panel. Self-fetches /api/v1/operations/orders.
 * Neither the RUN-status `status` query param nor any other server-side
 * filter narrows to "open order lifecycle state", so this panel filters
 * client-side via OPEN_STATUSES and discloses any hidden terminal-status
 * rows rather than silently dropping them, per the plan's honesty
 * constraint.
 */
export function OpenOrdersPanel() {
  const { loading, result, refetch } =
    useApiQuery<CollectionResponse<OrderItem>>(ENDPOINT);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Open Orders</h2>
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
          <ErrorState failure={result} title="Failed to load orders" />
        ) : (
          <OpenOrdersContent data={result.data} />
        )}
      </div>
    </section>
  );
}

function OpenOrdersContent({ data }: { data: CollectionResponse<OrderItem> }) {
  const [revealed, setRevealed] = useState(false);
  const items = data.items;
  const open = items.filter((o) => OPEN_STATUSES.has(o.status));
  const hiddenCount = items.length - open.length;
  const capped = data.count >= 100;

  const rows = revealed ? items : open;

  return (
    <div className="space-y-3">
      {capped ? (
        <p className="text-xs text-amber-300">
          Showing the 100 most-recent orders; older open orders may be
          truncated.
        </p>
      ) : null}

      {hiddenCount > 0 ? (
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <span>
            {revealed
              ? `Showing all ${items.length} order(s), including ${hiddenCount} non-open.`
              : `Hiding ${hiddenCount} non-open order(s) (filled/canceled/rejected/expired/…).`}
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
            ? "No open orders."
            : "No open orders among the 100 most-recent; older open orders may be truncated."}
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-zinc-800 text-zinc-500">
              <th className="py-1 pr-2 font-normal">Symbol</th>
              <th className="py-1 pr-2 font-normal">Side</th>
              <th className="py-1 pr-2 font-normal">Qty</th>
              <th className="py-1 pr-2 font-normal">Status</th>
              <th className="py-1 pr-2 font-normal">Submitted</th>
              <th className="py-1 font-normal">Client order id</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr
                key={o.order_id}
                className={
                  revealed && !OPEN_STATUSES.has(o.status)
                    ? "border-t border-zinc-800 align-top text-zinc-500"
                    : "border-t border-zinc-800 align-top text-zinc-200"
                }
              >
                <td className="py-1 pr-2">{o.symbol}</td>
                <td className="py-1 pr-2">{o.side}</td>
                <td className="py-1 pr-2">{o.quantity}</td>
                <td className="py-1 pr-2">
                  {o.status}
                  {o.broker_status ? ` / ${o.broker_status}` : ""}
                </td>
                <td className="py-1 pr-2">{o.submitted_at ?? "—"}</td>
                <td className="py-1">
                  <div className="font-mono">{o.client_order_id}</div>
                  {o.last_submission_error ? (
                    <div className="mt-0.5 text-red-300">
                      submission error: {o.last_submission_error}
                    </div>
                  ) : null}
                  {o.last_sync_error ? (
                    <div className="mt-0.5 text-red-300">
                      sync error: {o.last_sync_error}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
