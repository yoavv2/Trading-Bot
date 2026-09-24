"use client";

// JOBUI-01: the closed 5-value JobStatus enum, exactly as declared in
// components/jobs/types.ts -- kept in sync manually since these are select
// option literals, not a type-level import.
const STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;

export type JobFiltersValue = {
  status: string;
  jobType: string;
};

type JobFiltersProps = JobFiltersValue & {
  // Job type options come from the live /api/v1/job-types catalog, never
  // hard-coded -- this is what keeps JobFilters job-type-agnostic (D-17).
  jobTypes: string[];
  onChange: (next: JobFiltersValue) => void;
};

/**
 * Presentational, controlled filter bar for the Jobs list (JOBUI-01),
 * mirroring RunFilters. Does not fetch -- JobsTable owns status/jobType
 * state and turns a change here into status=/job_type= query params applied
 * server-side.
 */
export function JobFilters({ status, jobType, jobTypes, onChange }: JobFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <label className="flex items-center gap-2 text-zinc-300">
        <span className="text-xs text-zinc-500">Status</span>
        <select
          value={status}
          onChange={(event) => onChange({ status: event.target.value, jobType })}
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-100"
        >
          <option value="">All statuses</option>
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-zinc-300">
        <span className="text-xs text-zinc-500">Job type</span>
        <select
          value={jobType}
          onChange={(event) => onChange({ status, jobType: event.target.value })}
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-100"
        >
          <option value="">All job types</option>
          {jobTypes.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
