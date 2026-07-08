"use client";

import { use } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import {
  RunHeaderPanel,
  type RunDetailResponse,
} from "@/components/runs/detail/RunHeaderPanel";
import { SignalsRiskPanel } from "@/components/runs/detail/SignalsRiskPanel";

type RunDetailPageProps = {
  params: Promise<{ runId: string }>;
};

/**
 * Run detail route shell (RUNS-03/RUNS-04): reads the `runId` route param (a
 * Promise in this Next.js version — see node_modules/next/dist/docs — resolved
 * here via React's `use()` since this is a Client Component page), fetches the
 * run once via `useApiQuery`, and composes the header + audit panels.
 *
 * The run fetch lives here rather than inside RunHeaderPanel so the resolved
 * `strategy_id` / `run_type` (the RunDetailContext contract) are available to
 * gate the sibling panels below without a second fetch of the same endpoint.
 * Child panels are only rendered once the run has resolved, so their query
 * strings are never built from an undefined strategyId.
 */
export default function RunDetailPage({ params }: RunDetailPageProps) {
  const { runId } = use(params);
  const { loading, result, refetch } = useApiQuery<RunDetailResponse>(
    `/api/v1/runs/${runId}`,
  );

  const run = result?.ok ? result.data.run : null;

  return (
    <main className="flex-1 space-y-4 p-6">
      <h1 className="text-xl font-semibold text-zinc-100">Run Detail</h1>

      <RunHeaderPanel loading={loading} result={result} refetch={refetch} />

      {run ? (
        <>
          <SignalsRiskPanel runId={runId} strategyId={run.strategy_id} />

          {/*
            14-04 drop-in point (RunDetailContext = { runId, strategyId: run.strategy_id, runType: run.run_type }):
            <OrdersFillsPanel runId={runId} strategyId={run.strategy_id} runType={run.run_type} />
            <MetricsPanel runId={runId} strategyId={run.strategy_id} runType={run.run_type} />
          */}
        </>
      ) : null}
    </main>
  );
}
