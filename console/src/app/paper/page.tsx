import { PaperAnalyticsSection } from "@/components/paper/PaperAnalyticsSection";

/**
 * Paper Trading Status screen (PAPR-03/PAPR-04): the operator sees the
 * latest account snapshot and reconciliation result without reading logs
 * or the DB. Positions + open orders panels land in 15-02.
 */
export default function PaperPage() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">
        Paper Trading Status
      </h1>
      <PaperAnalyticsSection />
    </main>
  );
}
