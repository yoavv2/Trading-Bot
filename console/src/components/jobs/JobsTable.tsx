"use client";

import { useState } from "react";
import Link from "next/link";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import {
  isTerminalJobStatus,
  jobStatusColor,
  JOB_STATUS_BADGE_CLASS,
} from "@/lib/jobStatus";
import {
  catalogEntryFor,
  outcomeToneClass,
  outcomeView,
} from "@/lib/jobOutcome";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import { AutoRefreshIndicator } from "./AutoRefreshIndicator";
import { JobFilters, type JobFiltersValue } from "./JobFilters";
import type { JobsResponse } from "./types";

const POLL_INTERVAL_SECONDS = 5;

function buildJobsEndpoint(status: string, jobType: string): string {
  const params = new URLSearchParams({ limit: "50" });
  if (status) params.set("status", status);
  if (jobType) params.set("job_type", jobType);
  return `/api/v1/jobs?${params.toString()}`;
}

/**
 * JOBUI-01/JOBUI-05: job-type-agnostic list over GET /api/v1/jobs, with
 * server-side status/job_type filtering, a mutation-capability-gated "New
 * Job" entry point (D-17/D-21), and auto-refresh while any visible row is
 * non-terminal. Owns its own status/jobType filter state -- the page shell
 * renders only <JobsTable /> (per this plan's page.tsx scope), so filter
 * state lives here rather than being lifted to the page as RunsPage does
 * for RunFilters/RunsTable.
 *
 * D-17: this component contains zero job_type conditionals -- job type
 * values flow through generically from the catalog (filter options) and the
 * response items (table cells), never compared or switched on.
 */
export function JobsTable() {
  const [filters, setFilters] = useState<JobFiltersValue>({
    status: "",
    jobType: "",
  });

  const endpoint = buildJobsEndpoint(filters.status, filters.jobType);
  const { loading, result, refetch, polling } = useApiQuery<JobsResponse>(endpoint, {
    pollIntervalMs: 5000,
    shouldPoll: (data) => data.items.some((job) => !isTerminalJobStatus(job.status)),
  });

  const capability = useMutationCapability();
  const jobTypes = capability.catalog?.items.map((item) => item.job_type) ?? [];

  const filtersActive = Boolean(filters.status) || Boolean(filters.jobType);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Jobs</h2>
        <div className="flex flex-wrap items-center gap-3">
          <AutoRefreshIndicator
            polling={polling}
            intervalSeconds={POLL_INTERVAL_SECONDS}
            stoppedText="Auto-refresh stopped — all visible Jobs finished"
          />
          <FetchMeta asOf={result?.asOf ?? null} loading={loading} onRefresh={refetch} />
          {capability.state === "enabled" ? (
            <Link
              href="/jobs/new"
              className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-300"
            >
              New Job
            </Link>
          ) : (
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled
                className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 disabled:cursor-not-allowed disabled:opacity-50"
              >
                New Job
              </button>
              {capability.reason ? (
                <span className="text-xs text-zinc-500">{capability.reason}</span>
              ) : null}
            </div>
          )}
        </div>
      </div>
      <div className="mt-3">
        <JobFilters
          status={filters.status}
          jobType={filters.jobType}
          jobTypes={jobTypes}
          onChange={setFilters}
        />
      </div>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !result.ok ? (
          <ErrorState failure={result} />
        ) : result.data.items.length === 0 ? (
          filtersActive ? (
            <div>
              <p className="text-sm text-zinc-300">No Jobs match these filters.</p>
              <p className="mt-1 text-xs text-zinc-500">
                Clear filters or submit a new Job.
              </p>
            </div>
          ) : (
            <div>
              <p className="text-sm text-zinc-300">No Jobs yet</p>
              <p className="mt-1 text-xs text-zinc-500">
                Submit a new Job to get started.
              </p>
            </div>
          )
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500">
                <th className="py-2 pr-4">Job type</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Queued</th>
                <th className="py-2 pr-4">Started</th>
                <th className="py-2 pr-4">Completed</th>
                <th className="py-2 pr-4">Failure</th>
                <th className="py-2 pr-4">Detail</th>
              </tr>
            </thead>
            <tbody>
              {result.data.items.map((job) => {
                // 20.1-14: Outcome next to the lifecycle status; lookups live in lib/jobOutcome.ts.
                const outcome = outcomeView(
                  job,
                  catalogEntryFor(capability.catalog, job.job_type),
                );
                return (
                <tr key={job.id} className="border-b border-zinc-900 text-zinc-300">
                  <td className="py-2 pr-4">{job.job_type}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`${JOB_STATUS_BADGE_CLASS} ${
                        outcome ? outcomeToneClass(outcome.tone) : jobStatusColor(job.status)
                      }`}
                    >
                      {outcome ? outcome.label : job.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4">{job.queued_at ?? "—"}</td>
                  <td className="py-2 pr-4">{job.started_at ?? "—"}</td>
                  <td className="py-2 pr-4">{job.completed_at ?? "—"}</td>
                  <td className="py-2 pr-4">
                    {job.failure_reason
                      ? `${job.failure_reason}${
                          job.outcome_uncertain ? " · outcome uncertain" : ""
                        }`
                      : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="text-xs font-semibold text-sky-400 hover:underline"
                    >
                      View
                    </Link>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
