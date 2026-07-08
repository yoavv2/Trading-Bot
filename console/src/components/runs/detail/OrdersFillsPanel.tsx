"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { CappedDisclosure } from "./CappedDisclosure";
import { filterByRun, isCapped } from "./runScopedFilter";

export type OrderItem = {
  order_id: string;
  run_id: string;
  strategy_id: string;
  run_type: string;
  run_status: string;
  symbol: string;
  session_date: string;
  side: string;
  quantity: string;
  order_type: string;
  time_in_force: string;
  status: string;
  broker_status: string | null;
  client_order_id: string;
  broker_order_id: string | null;
  submitted_at: string | null;
  filled_at: string | null;
  source_risk_event_id: string;
  submission_attempt_count: number;
  sync_failure_count: number;
  last_submission_error: string | null;
  last_sync_error: string | null;
  intent_context: {
    intent_hash: string | null;
    intent_version: number | null;
    supersedes_paper_order_id: string | null;
    supersedes_client_order_id: string | null;
  };
};

type OrdersResponse = {
  filters: Record<string, unknown>;
  count: number;
  items: OrderItem[];
};

export type FillItem = {
  fill_id: string;
  paper_order_id: string;
  run_id: string;
  strategy_id: string;
  run_type: string;
  run_status: string;
  symbol: string;
  session_date: string;
  side: string;
  quantity: string;
  price: string;
  filled_at: string;
  broker_fill_id: string | null;
  broker_order_id: string | null;
  order_status: string;
};

type FillsResponse = {
  filters: Record<string, unknown>;
  count: number;
  items: FillItem[];
};

type OrdersFillsPanelProps = {
  runId: string;
  strategyId: string;
};

/**
 * RUNS-05: this run's orders (including client_order_id intent lineage via
 * intent_context.supersedes_client_order_id) and fills. Neither
 * /api/v1/operations/orders nor /api/v1/operations/fills has a run_id filter,
 * so each sub-section fetches strategy-wide (limit=100) and filters
 * client-side via filterByRun -- surfacing the shared CappedDisclosure
 * whenever the RAW response hit the 100-row cap, even when the filtered
 * result is empty (an old run outside the 100-row window must read "may be
 * truncated," never "no orders/fills for this run").
 */
export function OrdersFillsPanel({ runId, strategyId }: OrdersFillsPanelProps) {
  return (
    <div className="space-y-4">
      <OrdersSection runId={runId} strategyId={strategyId} />
      <FillsSection runId={runId} strategyId={strategyId} />
    </div>
  );
}

function OrdersSection({ runId, strategyId }: OrdersFillsPanelProps) {
  const endpoint = `/api/v1/operations/orders?strategy_id=${strategyId}&limit=100`;
  const { loading, result, refetch } = useApiQuery<OrdersResponse>(endpoint);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Orders</h2>
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
          <OrdersContent data={result.data} runId={runId} />
        )}
      </div>
    </section>
  );
}

function OrdersContent({
  data,
  runId,
}: {
  data: OrdersResponse;
  runId: string;
}) {
  const matched = filterByRun(data.items, runId);
  const capped = isCapped(data.count);

  return (
    <div className="space-y-3">
      <CappedDisclosure
        capped={capped}
        rowNoun="orders"
        matchedCount={matched.length}
      />

      {matched.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {capped
            ? "No matching orders in the 100 most-recent."
            : "No orders recorded for this run."}
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-zinc-500">
              <th className="pb-1 pr-2">Symbol</th>
              <th className="pb-1 pr-2">Side</th>
              <th className="pb-1 pr-2">Qty</th>
              <th className="pb-1 pr-2">Type</th>
              <th className="pb-1 pr-2">Status</th>
              <th className="pb-1 pr-2">Submitted</th>
              <th className="pb-1 pr-2">Filled</th>
              <th className="pb-1">Client order id / lineage</th>
            </tr>
          </thead>
          <tbody>
            {matched.map((item) => (
              <tr
                key={item.order_id}
                className="border-t border-zinc-800 align-top"
              >
                <td className="py-1 pr-2 text-zinc-200">{item.symbol}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.side}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.quantity}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.order_type}</td>
                <td className="py-1 pr-2 text-zinc-300">
                  {item.status}
                  {item.broker_status ? ` / ${item.broker_status}` : ""}
                </td>
                <td className="py-1 pr-2 text-zinc-300">
                  {item.submitted_at ?? "—"}
                </td>
                <td className="py-1 pr-2 text-zinc-300">
                  {item.filled_at ?? "—"}
                </td>
                <td className="py-1 text-zinc-300">
                  <div className="font-mono">{item.client_order_id}</div>
                  {item.intent_context.supersedes_client_order_id ? (
                    <div className="mt-0.5 font-mono text-amber-300">
                      supersedes: {item.intent_context.supersedes_client_order_id}
                    </div>
                  ) : null}
                  {item.last_submission_error ? (
                    <div className="mt-0.5 text-red-300">
                      submission error: {item.last_submission_error}
                    </div>
                  ) : null}
                  {item.last_sync_error ? (
                    <div className="mt-0.5 text-red-300">
                      sync error: {item.last_sync_error}
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

function FillsSection({ runId, strategyId }: OrdersFillsPanelProps) {
  const endpoint = `/api/v1/operations/fills?strategy_id=${strategyId}&limit=100`;
  const { loading, result, refetch } = useApiQuery<FillsResponse>(endpoint);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Fills</h2>
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
          <ErrorState failure={result} title="Failed to load fills" />
        ) : (
          <FillsContent data={result.data} runId={runId} />
        )}
      </div>
    </section>
  );
}

function FillsContent({
  data,
  runId,
}: {
  data: FillsResponse;
  runId: string;
}) {
  const matched = filterByRun(data.items, runId);
  const capped = isCapped(data.count);

  return (
    <div className="space-y-3">
      <CappedDisclosure
        capped={capped}
        rowNoun="fills"
        matchedCount={matched.length}
      />

      {matched.length === 0 ? (
        <p className="text-sm text-zinc-500">
          {capped
            ? "No matching fills in the 100 most-recent."
            : "No fills recorded for this run."}
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-zinc-500">
              <th className="pb-1 pr-2">Symbol</th>
              <th className="pb-1 pr-2">Side</th>
              <th className="pb-1 pr-2">Qty</th>
              <th className="pb-1 pr-2">Price</th>
              <th className="pb-1 pr-2">Filled at</th>
              <th className="pb-1">Broker fill id</th>
            </tr>
          </thead>
          <tbody>
            {matched.map((item) => (
              <tr key={item.fill_id} className="border-t border-zinc-800">
                <td className="py-1 pr-2 text-zinc-200">{item.symbol}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.side}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.quantity}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.price}</td>
                <td className="py-1 pr-2 text-zinc-300">{item.filled_at}</td>
                <td className="py-1 font-mono text-zinc-300">
                  {item.broker_fill_id ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
