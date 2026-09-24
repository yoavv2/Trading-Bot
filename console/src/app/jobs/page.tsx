"use client";

import { JobsTable } from "@/components/jobs/JobsTable";

/**
 * Jobs screen (JOBUI-01): the generic Job list over GET /api/v1/jobs,
 * filterable by status/job_type and self-refreshing while any Job is
 * non-terminal (JOBUI-05).
 */
export default function JobsPage() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">Jobs</h1>
      <JobsTable />
    </main>
  );
}
