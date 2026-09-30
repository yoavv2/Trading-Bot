import { StrategyOverviewPanel } from "@/components/strategy/StrategyOverviewPanel";

/**
 * Strategy overview screen (STRA-01/STRA-02): lets the operator select any
 * registered strategy, then shows its live control status and declared config
 * summary (universe, entry/indicator rules, exit rules, risk params).
 */
export default function StrategyPage() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">Strategy</h1>
      <StrategyOverviewPanel />
    </main>
  );
}
