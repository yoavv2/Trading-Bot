"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { NewJobView } from "@/components/jobs/new/NewJobView";

/**
 * Reads `type` and any other query params (e.g. `strategy_id`, D-18) via
 * useSearchParams -- a Client Component hook that must live under a
 * Suspense boundary in this Next.js version (see NewJobPage below).
 */
function NewJobRoute() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const jobType = searchParams.get("type");
  const initialParams: Record<string, string> = {};
  for (const [key, value] of searchParams.entries()) {
    if (key !== "type") {
      initialParams[key] = value;
    }
  }

  return (
    <NewJobView
      jobType={jobType}
      initialParams={initialParams}
      onNavigate={(href) => router.push(href)}
    />
  );
}

/** /jobs/new route shell (D-17/OPS-01). */
export default function NewJobPage() {
  return (
    <main className="flex-1 p-6">
      <Suspense fallback={null}>
        <NewJobRoute />
      </Suspense>
    </main>
  );
}
