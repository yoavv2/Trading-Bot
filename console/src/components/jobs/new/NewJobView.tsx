"use client";

import Link from "next/link";
import { useApiQuery } from "@/lib/useApiQuery";
import { mutationCapabilityFrom, type MutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import { JOB_TYPE_FORMS } from "@/lib/jobTypeForms";
import { API_ONLY_NOTICE, catalogEntryFor, isApiOnly } from "@/lib/jobOutcome";
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

  if (jobType !== null) {
    // 20.1-14 (D-31): an api_only catalog type is never submitted from this console, even
    // when a form were registered -- check the catalog entry before the form lookup. Only a
    // type the loaded catalog knows is api_only reaches the notice; the not-in-catalog case
    // keeps the unmapped-type fallback below.
    const apiOnlyEntry = result.ok ? catalogEntryFor(result.data, jobType) : undefined;
    if (isApiOnly(apiOnlyEntry)) {
      return (
        <div>
          <p className="text-sm text-zinc-300">{API_ONLY_NOTICE}</p>
          <Link
            href="/jobs"
            className="mt-2 inline-block text-xs font-semibold text-sky-400 hover:underline"
          >
            Back to Jobs
          </Link>
        </div>
      );
    }

    // The unmapped-type fallback copy is static and does not depend on the
    // catalog having loaded successfully -- check it before branching on
    // result.ok so an unmapped type always renders the same way.
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

    // D-21: a catalog fetch failure must not hide a registered form's
    // submission control -- it stays visible but disabled, with the
    // capability's honest "unknown" reason (mutationCapabilityFrom already
    // derives that copy from a confirmed failed fetch). Only the type
    // picker below (jobType === null) needs a successful catalog to render
    // at all.
    const catalogEntry = result.ok ? catalogEntryFor(result.data, jobType) : undefined;

    return (
      <FormComponent
        catalogEntry={catalogEntry}
        capability={capability}
        initialParams={initialParams}
        onNavigate={onNavigate}
      />
    );
  }

  if (!result.ok) {
    return <ErrorState failure={result} />;
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">New Job</h1>
      <ul className="space-y-3">
        {result.data.items.map((item) => (
          <li key={item.job_type}>
            <Link
              href={`/jobs/new?type=${encodeURIComponent(item.job_type)}`}
              className="text-xs font-semibold text-sky-400 hover:underline"
            >
              {item.job_type}
            </Link>
            {item.console_submission === "api_only" ? (
              <span className="ml-2 text-xs text-zinc-500">API only</span>
            ) : null}
            <p className="mt-1 text-sm text-zinc-400">{item.description}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
