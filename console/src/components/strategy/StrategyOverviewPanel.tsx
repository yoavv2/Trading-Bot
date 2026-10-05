"use client";

import { useState } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { StrategyStatusBadge } from "@/components/strategy/StrategyStatusBadge";
import { StrategyControlTrigger } from "@/components/controls/StrategyControlTrigger";
import { useStrategyControlState } from "@/components/controls/useStrategyControlState";
import { useLastKnownData } from "@/lib/useLastKnownData";
import { ActivePaperStrategyLine } from "@/components/controls/ActivePaperStrategyLine";
import { JobShortcutLink } from "@/components/shortcuts/JobShortcutLink";

const DEFAULT_STRATEGY_ID = "trend_following_daily";

type Strategy = {
  strategy_id: string;
  display_name: string;
  version: string;
  enabled: boolean;
  description: string;
  config_reference: string;
  universe: string[];
  universe_size: number;
  indicators: Record<string, unknown>;
  risk: Record<string, unknown>;
  exits: Record<string, unknown>;
};

type StrategiesResponse = {
  count: number;
  strategies: Strategy[];
};

/** Renders a generic key/value table for an open-ended config dict (STRA-02). */
function KeyValueSection({
  title,
  data,
}: {
  title: string;
  data: Record<string, unknown>;
}) {
  const entries = Object.entries(data);
  return (
    <section className="mt-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h3>
      {entries.length === 0 ? (
        <p className="mt-1 text-sm text-zinc-500">None configured.</p>
      ) : (
        <dl className="mt-2 grid grid-cols-[minmax(0,auto)_1fr] gap-x-4 gap-y-1 text-sm">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-zinc-500">{key}</dt>
              <dd className="font-mono text-xs text-zinc-200">
                {typeof value === "object" && value !== null
                  ? JSON.stringify(value)
                  : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

/**
 * Keyed per strategy so hooks always run legally and local dialog state can
 * never carry across a strategy selection. Within one selected strategy the
 * control trigger remains at one stable JSX position across status re-reads
 * (WR-C-06), including failed re-reads via the last known control state.
 */
function SelectedStrategyDetail({ strategy }: { strategy: Strategy }) {
  const controlState = useStrategyControlState(strategy.strategy_id);
  const lastKnownControl = useLastKnownData(controlState.result);

  return (
    <div className="mt-4 border-t border-zinc-800 pt-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-base font-semibold text-zinc-100">
              {strategy.display_name}
            </h3>
            <span className="text-xs text-zinc-500">{strategy.version}</span>
            {controlState.result?.ok ? (
              <StrategyStatusBadge
                enabled={controlState.result.data.status === "enabled"}
              />
            ) : null}
            {lastKnownControl ? (
              <StrategyControlTrigger
                strategyId={strategy.strategy_id}
                enabled={lastKnownControl.status === "enabled"}
                stateKnown={controlState.result?.ok === true}
              />
            ) : null}
            {controlState.result && !controlState.result.ok ? (
              <span className="text-xs text-zinc-500">
                Control state unavailable
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm text-zinc-300">{strategy.description}</p>
          <p className="mt-1 break-all font-mono text-xs text-zinc-500">
            {strategy.config_reference}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <JobShortcutLink
            jobType="backtest"
            strategyId={strategy.strategy_id}
            label="Run backtest"
          />
          <JobShortcutLink
            jobType="risk-evaluation"
            strategyId={strategy.strategy_id}
            label="Evaluate risk"
          />
        </div>
      </div>

      <section className="mt-4">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Universe ({strategy.universe_size})
        </h3>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {strategy.universe.map((ticker) => (
            <span
              key={ticker}
              className="rounded border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 font-mono text-xs text-zinc-200"
            >
              {ticker}
            </span>
          ))}
        </div>
      </section>

      <KeyValueSection title="Entry / Indicators" data={strategy.indicators} />
      <KeyValueSection title="Exit Rules" data={strategy.exits} />
      <KeyValueSection title="Risk Params" data={strategy.risk} />
    </div>
  );
}

/**
 * Strategy catalog and selected-strategy overview (STRA-01/STRA-02). The
 * catalog supplies the open-ended metadata while effective ENABLED/DISABLED
 * state still comes exclusively from the live DB control read.
 */
export function StrategyOverviewPanel() {
  const { loading, result, refetch } =
    useApiQuery<StrategiesResponse>("/api/v1/strategies");
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | null>(
    null,
  );

  const strategies = result?.ok ? result.data.strategies : [];
  const selectedStrategy =
    strategies.find(
      (strategy) => strategy.strategy_id === selectedStrategyId,
    ) ??
    strategies.find(
      (strategy) => strategy.strategy_id === DEFAULT_STRATEGY_ID,
    ) ??
    strategies[0] ??
    null;

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex flex-col gap-3 border-b border-zinc-800 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-zinc-200">
            Strategy Overview
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Inspect configuration and operate a registered strategy.
          </p>
          <div className="mt-1">
            <ActivePaperStrategyLine />
          </div>
        </div>
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>

      <div className="mt-4">
        {!result ? (
          <p className="text-sm text-zinc-500" role="status">
            Loading strategies…
          </p>
        ) : !result.ok ? (
          <ErrorState failure={result} title="Strategy catalog unavailable" />
        ) : strategies.length === 0 ? (
          <div className="rounded border border-dashed border-zinc-700 px-4 py-6 text-center">
            <p className="text-sm font-medium text-zinc-300">
              No strategies registered
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              Add an enabled strategy configuration to make it available here.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2 rounded border border-zinc-800 bg-zinc-950/40 p-3 sm:flex-row sm:items-end sm:justify-between">
              <div className="w-full sm:max-w-md">
                <label
                  htmlFor="strategy-overview-select"
                  className="block text-xs font-semibold uppercase tracking-wide text-zinc-500"
                >
                  Selected strategy
                </label>
                <select
                  id="strategy-overview-select"
                  value={selectedStrategy?.strategy_id ?? ""}
                  onChange={(event) => setSelectedStrategyId(event.target.value)}
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500"
                >
                  {strategies.map((strategy) => (
                    <option
                      key={strategy.strategy_id}
                      value={strategy.strategy_id}
                    >
                      {strategy.display_name} ({strategy.strategy_id})
                    </option>
                  ))}
                </select>
              </div>
              <p className="text-xs text-zinc-500">
                {result.data.count} registered{" "}
                {result.data.count === 1 ? "strategy" : "strategies"}
              </p>
            </div>

            {selectedStrategy ? (
              <SelectedStrategyDetail
                key={selectedStrategy.strategy_id}
                strategy={selectedStrategy}
              />
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
