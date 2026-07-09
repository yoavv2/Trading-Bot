"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { PaperAccountPanel } from "./PaperAccountPanel";
import { PaperReconciliationPanel } from "./PaperReconciliationPanel";
import type { AnalyticsResponse } from "./types";

/**
 * Analytics-fed half of the Paper Trading Status screen (PAPR-03/PAPR-04).
 * Owns a SINGLE useApiQuery call against the analytics endpoint and shares
 * the result between both sub-panels (14-03 precedent: the owner fetches
 * once instead of two panels double-fetching the same endpoint). One
 * endpoint, one honest ErrorState naming it on failure — no blank or
 * fake-success render.
 */
export function PaperAnalyticsSection() {
  const { loading, result, refetch } = useApiQuery<AnalyticsResponse>("/api/v1/analytics/strategies/trend_following_daily");

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">
          Account & Reconciliation
        </h2>
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
          <ErrorState failure={result} />
        ) : (
          (() => {
            const paper = result.data.paper;
            return (
              <div className="space-y-6">
                <section>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Account snapshot
                  </h3>
                  <PaperAccountPanel
                    snapshot={paper?.latest_account_snapshot ?? null}
                  />
                </section>
                <section>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Reconciliation
                  </h3>
                  <PaperReconciliationPanel
                    reconciliation={paper?.latest_reconciliation ?? null}
                    findings={paper?.recent_execution_findings ?? []}
                  />
                </section>
              </div>
            );
          })()
        )}
      </div>
    </section>
  );
}
