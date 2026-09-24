"use client";

import { use } from "react";
import { JobDetailView } from "@/components/jobs/detail/JobDetailView";

type JobDetailPageProps = {
  params: Promise<{ jobId: string }>;
};

/**
 * Job detail route shell (JOBUI-02). Reads the `jobId` route param (a
 * Promise in this Next.js version -- see node_modules/next/dist/docs --
 * resolved here via React's `use()` since this is a Client Component page)
 * and composes the generic detail view. Matches the existing
 * `app/runs/[runId]/page.tsx` shape.
 */
export default function JobDetailPage({ params }: JobDetailPageProps) {
  const { jobId } = use(params);

  return (
    <main className="flex-1 p-6">
      <JobDetailView jobId={jobId} />
    </main>
  );
}
