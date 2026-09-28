"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { StrategyStatusBadge } from "@/components/strategy/StrategyStatusBadge";
import { StrategyControlTrigger } from "@/components/controls/StrategyControlTrigger";
import { useStrategyControlState } from "@/components/controls/useStrategyControlState";
import { useLastKnownData } from "@/lib/useLastKnownData";
import { JobShortcutLink } from "@/components/shortcuts/JobShortcutLink";

const STRATEGY_ID = "trend_following_daily";

type StrategyDetail = {
  strategy: {
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
  operator_reads: Record<string, unknown>;
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
 * Strategy overview screen panel (STRA-01/STRA-02): fetches the declared
 * config for TrendFollowingDailyV1 and renders its universe and
 * entry/exit/risk config generically. The ENABLED/DISABLED badge and the
 * inline Enable/Disable trigger read the DB control status
 * (`useStrategyControlState`), never the static config `enabled` flag; when
 * that read fails the panel shows "Control state unavailable" and no trigger
 * (no target state can be computed without a known current state). The
 * trigger sits at one stable JSX position and receives `enabled` as a prop
 * (20-18 caller constraint); it also stays mounted across a failed re-read
 * (WR-C-06) so an open confirm dialog survives, with only the badge and the
 * trigger button withheld (`stateKnown={false}`). Composed directly from
 * the shared lib primitives (useApiQuery/FetchMeta/ErrorState) rather than
 * the status-screen-scoped StatusPanel wrapper.
 */
export function StrategyOverviewPanel() {
  const { loading, result, refetch } = useApiQuery<StrategyDetail>("/api/v1/strategies/trend_following_daily");
  const controlState = useStrategyControlState(STRATEGY_ID);
  const lastKnownControl = useLastKnownData(controlState.result);
  const strategyId = result?.ok ? result.data.strategy.strategy_id : null;

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">
          Strategy Overview
        </h2>
        <div className="flex items-center gap-3">
          {strategyId ? (
            <>
              <JobShortcutLink
                jobType="backtest"
                strategyId={strategyId}
                label="Run backtest"
              />
              <JobShortcutLink
                jobType="risk-evaluation"
                strategyId={strategyId}
                label="Evaluate risk"
              />
            </>
          ) : null}
          <FetchMeta
            asOf={result?.asOf ?? null}
            loading={loading}
            onRefresh={refetch}
          />
        </div>
      </div>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !result.ok ? (
          <ErrorState failure={result} />
        ) : (
          (() => {
            const { strategy } = result.data;
            return (
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h3 className="text-base font-semibold text-zinc-100">
                    {strategy.display_name}
                  </h3>
                  <span className="text-xs text-zinc-500">
                    {strategy.version}
                  </span>
                  {controlState.result?.ok ? (
                    <StrategyStatusBadge
                      enabled={controlState.result.data.status === "enabled"}
                    />
                  ) : null}
                  {lastKnownControl ? (
                    <StrategyControlTrigger
                      strategyId={STRATEGY_ID}
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

                <p className="mt-2 text-sm text-zinc-300">
                  {strategy.description}
                </p>
                <p className="mt-1 font-mono text-xs text-zinc-500">
                  {strategy.config_reference}
                </p>

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

                <KeyValueSection
                  title="Entry / Indicators"
                  data={strategy.indicators}
                />
                <KeyValueSection title="Exit Rules" data={strategy.exits} />
                <KeyValueSection title="Risk Params" data={strategy.risk} />
              </div>
            );
          })()
        )}
      </div>
    </section>
  );
}
