"use client";

import Link from "next/link";
import { useApiQuery } from "@/lib/useApiQuery";
import { mutationCapabilityFrom, type MutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import { JOB_TYPE_FORMS } from "@/lib/jobTypeForms";
import type { JobTypesCatalog } from "@/components/jobs/types";

const JOB_TYPES_ENDPOINT = "/api/v1/job-types";

type NewJobViewProps = {
  jobType: string | null;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

/**
 * D-17: the sole entry point that dispatches through lookup map (1)
 * (JOB_TYPE_FORMS, jobTypeForms.ts) -- this is the only file in the console
 * permitted to import that module. Fetches the job-types catalog directly
 * (rather than via useMutationCapability) so a catalog fetch failure can
 * render the standard ErrorState with full failure detail, while reusing
 * the same exported mutationCapabilityFrom() pure mapping
 * useMutationCapability uses internally to derive the MutationCapability
 * handed down to the selected submission form.
 *
 * Renders the catalog-driven type picker when `jobType` is null, the
 * matching submission form once one is selected and registered in map (1),
 * or the "no form registered" fallback for an unmapped type (Phase 20 adds
 * more map entries; this file's dispatch logic does not change).
 */
export function NewJobView({ jobType, initialParams, onNavigate }: NewJobViewProps) {
  const { loading, result } = useApiQuery<JobTypesCatalog>(JOB_TYPES_ENDPOINT);
  const capability: MutationCapability = { ...mutationCapabilityFrom(result), loading };

  if (!result) {
    return <p className="text-sm text-zinc-500">Loading…</p>;
  }
  if (!result.ok) {
    return <ErrorState failure={result} />;
  }

  const catalog = result.data;

  if (jobType === null) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-semibold text-zinc-100">New Job</h1>
        <ul className="space-y-3">
          {catalog.items.map((item) => (
            <li key={item.job_type}>
              <Link
                href={`/jobs/new?type=${encodeURIComponent(item.job_type)}`}
                className="text-xs font-semibold text-sky-400 hover:underline"
              >
                {item.job_type}
              </Link>
              <p className="mt-1 text-sm text-zinc-400">{item.description}</p>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const FormComponent = JOB_TYPE_FORMS[jobType];
  if (!FormComponent) {
    return (
      <div>
        <p className="text-sm text-zinc-300">
          {`No submission form is available for "${jobType}" yet.`}
        </p>
        <Link
          href="/jobs"
          className="mt-2 inline-block text-xs font-semibold text-sky-400 hover:underline"
        >
          Back to Jobs
        </Link>
      </div>
    );
  }

  const catalogEntry = catalog.items.find((item) => item.job_type === jobType);

  return (
    <FormComponent
      catalogEntry={catalogEntry}
      capability={capability}
      initialParams={initialParams}
      onNavigate={onNavigate}
    />
  );
}
