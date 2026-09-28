"use client";

import { useState } from "react";
import { JobFormFooter, useJobFormSubmission } from "./jobFormKit";
import type { JobTypeCatalogItem } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

// Structurally identical to the job-type lookup map's JobSubmissionFormProps
// -- declared locally (rather than imported) so this file never references
// the job-type lookup map module (only NewJobView.tsx is permitted to
// import it).
type SyncMarketSessionsJobFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

/**
 * OPS-05/D-25: the `sync-market-sessions` Job type's submission form. Both
 * fields (from_date, to_date) are required -- the backend never defaults
 * them inside validate_payload, so the Job payload records exactly what
 * will run. Both pre-fill from the catalog's submission_defaults when
 * present, else stay empty. Composes the shared jobFormKit mechanics
 * (Plan 06) rather than duplicating BacktestJobForm's submission logic. No
 * screen shortcut owns this Job type (UI-SPEC) -- `initialParams` is part
 * of the shared form-props shape but unused here.
 */
export function SyncMarketSessionsJobForm({
  catalogEntry,
  capability,
  onNavigate,
}: SyncMarketSessionsJobFormProps) {
  const [fromDate, setFromDate] = useState(
    catalogEntry?.submission_defaults?.from_date ?? "",
  );
  const [toDate, setToDate] = useState(
    catalogEntry?.submission_defaults?.to_date ?? "",
  );

  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "sync-market-sessions",
    onNavigate,
  });

  const canSubmit =
    fromDate.length > 0 && toDate.length > 0 && capability.state === "enabled";

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    void submit({ from_date: fromDate, to_date: toDate });
  }

  return (
    <div className="max-w-md space-y-4">
      <div>
        <label
          htmlFor="sync-market-sessions-from-date"
          className="block text-xs text-zinc-500"
        >
          From date
        </label>
        <input
          id="sync-market-sessions-from-date"
          type="date"
          value={fromDate}
          onChange={(event) => setFromDate(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <div>
        <label
          htmlFor="sync-market-sessions-to-date"
          className="block text-xs text-zinc-500"
        >
          To date
        </label>
        <input
          id="sync-market-sessions-to-date"
          type="date"
          value={toDate}
          onChange={(event) => setToDate(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <JobFormFooter
        capability={capability}
        outcome={outcome}
        canSubmit={canSubmit}
        submitting={submitting}
        label="Submit Sync Market Sessions"
        onSubmit={handleSubmit}
      />
    </div>
  );
}
