"use client";

import { JobsTable } from "@/components/jobs/JobsTable";

/**
 * Jobs screen (JOBUI-01): the generic Job list over GET /api/v1/jobs,
 * filterable by status/job_type and self-refreshing while any Job is
 * non-terminal (JOBUI-05).
 */
export default function JobsPage() {
  return (
    <main className="page-content">
      <div className="mb-6"><p className="eyebrow">Workspace tools</p><h1 className="page-heading mt-2">System activity</h1><p className="muted mt-2 text-sm">Track background tasks, investigate issues and manage maintenance. Study results are on your dashboard.</p></div>
      <JobsTable />
    </main>
  );
}
