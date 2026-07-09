import { PaperAnalyticsSection } from "@/components/paper/PaperAnalyticsSection";
import { PositionsPanel } from "@/components/paper/PositionsPanel";
import { OpenOrdersPanel } from "@/components/paper/OpenOrdersPanel";

/**
 * Paper Trading Status screen (PAPR-01/PAPR-02/PAPR-03/PAPR-04): the
 * operator sees the latest account snapshot, reconciliation result,
 * current positions, and open orders without reading logs or the DB.
 */
export default function PaperPage() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">
        Paper Trading Status
      </h1>
      <div className="space-y-6">
        <PaperAnalyticsSection />
        <PositionsPanel />
        <OpenOrdersPanel />
      </div>
    </main>
  );
}
