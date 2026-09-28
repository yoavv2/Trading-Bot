"use client";

import { useRouter } from "next/navigation";
import { useApiQuery } from "@/lib/useApiQuery";
import { isTerminalJobStatus } from "@/lib/jobStatus";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { AutoRefreshIndicator } from "../AutoRefreshIndicator";
import { JobHeaderPanel } from "./JobHeaderPanel";
import { JobProgressPanel } from "./JobProgressPanel";
import { JobResourcesPanel } from "./JobResourcesPanel";
import { JobResultSummaryPanel } from "./JobResultSummaryPanel";
import { JobLogsPanel } from "./JobLogsPanel";
import { JobEventsPanel } from "./JobEventsPanel";
import type { JobDetail } from "../types";

const POLL_INTERVAL_SECONDS = 3;

type JobDetailViewProps = {
  jobId: string;
};

/**
 * JOBUI-02/JOBUI-05: composes the generic Job detail screen from the Plan
 * 08/10 primitives over a single polled GET /api/v1/jobs/{id}. Polls every
 * 3s while the Job is non-terminal and stops the instant it observes a
 * terminal status. Zero job_type conditionals anywhere in this
 * composition -- every panel renders generically off JobDetail.
 */
export function JobDetailView({ jobId }: JobDetailViewProps) {
  const router = useRouter();
  const endpoint = `/api/v1/jobs/${encodeURIComponent(jobId)}`;
  const { loading, result, refetch, polling } = useApiQuery<JobDetail>(endpoint, {
    pollIntervalMs: 3000,
    shouldPoll: (data) => !isTerminalJobStatus(data.status),
  });

  const job = result?.ok ? result.data : null;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <AutoRefreshIndicator
          polling={polling}
          intervalSeconds={POLL_INTERVAL_SECONDS}
          stoppedText="Auto-refresh stopped — Job finished"
        />
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>

      {!result ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : !result.ok ? (
        <ErrorState failure={result} title="Failed to load Job" />
      ) : job ? (
        <>
          <JobHeaderPanel
            job={job}
            onChanged={refetch}
            onNavigate={(href) => router.push(href)}
          />
          <JobProgressPanel progress={job.progress} status={job.status} />
          <JobResourcesPanel resources={job.resources} status={job.status} />
          <JobResultSummaryPanel
            resultSummary={job.result_summary}
            status={job.status}
            resourceCount={job.resources.length}
          />
          <JobLogsPanel
            jobId={jobId}
            jobIsTerminal={isTerminalJobStatus(job.status)}
          />
          <JobEventsPanel
            jobId={jobId}
            jobIsTerminal={isTerminalJobStatus(job.status)}
          />
        </>
      ) : null}
    </div>
  );
}
