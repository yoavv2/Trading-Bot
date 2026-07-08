import { StrategyOverviewPanel } from "@/components/strategy/StrategyOverviewPanel";

/**
 * Strategy overview screen (STRA-01/STRA-02): shows TrendFollowingDailyV1's
 * enabled/disabled status and its declared config summary (universe,
 * entry/indicator rules, exit rules, risk params) without reading code or
 * the DB.
 */
export default function StrategyPage() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">Strategy</h1>
      <StrategyOverviewPanel />
    </main>
  );
}
