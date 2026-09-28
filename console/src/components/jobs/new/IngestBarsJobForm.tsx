"use client";

import { useState } from "react";
import {
  JobFormFooter,
  SYMBOLS_EMPTY_HELP,
  parseSymbolsInput,
  useJobFormSubmission,
} from "./jobFormKit";
import type { JobTypeCatalogItem } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

// Structurally identical to the job-type lookup map's JobSubmissionFormProps
// -- declared locally (rather than imported) so this file never references
// the job-type lookup map module (only NewJobView.tsx is permitted to
// import it).
type IngestBarsJobFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

/**
 * D-24/D-25: the `ingest-bars` Job type's submission form. All three fields
 * (from_date, to_date, symbols) are required -- the backend never defaults
 * them inside validate_payload, so the Job payload records exactly what
 * will run. All three pre-fill from the catalog's submission_defaults when
 * present, else stay empty. `symbols` is edited as a single comma-separated
 * text field but always submitted as a normalized `string[]`
 * (`parseSymbolsInput`, UI-SPEC symbols wire-shape note) -- never a string.
 * Composes the shared jobFormKit mechanics (Plan 06) rather than
 * duplicating BacktestJobForm's submission logic.
 */
// `initialParams` is part of the shared form-props shape (see the type
// comment above) but unused here -- `ingest-bars` has no strategy_id/deep-
// link field for any screen to pre-fill (UI-SPEC: no screen shortcut).
export function IngestBarsJobForm({
  catalogEntry,
  capability,
  onNavigate,
}: IngestBarsJobFormProps) {
  const [fromDate, setFromDate] = useState(
    catalogEntry?.submission_defaults?.from_date ?? "",
  );
  const [toDate, setToDate] = useState(
    catalogEntry?.submission_defaults?.to_date ?? "",
  );
  const [symbolsText, setSymbolsText] = useState(
    catalogEntry?.submission_defaults?.symbols ?? "",
  );

  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "ingest-bars",
    onNavigate,
  });

  const symbols = parseSymbolsInput(symbolsText);
  const canSubmit =
    fromDate.length > 0 &&
    toDate.length > 0 &&
    symbols.length > 0 &&
    capability.state === "enabled";

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    void submit({ from_date: fromDate, to_date: toDate, symbols });
  }

  return (
    <div className="max-w-md space-y-4">
      <div>
        <label
          htmlFor="ingest-bars-from-date"
          className="block text-xs text-zinc-500"
        >
          From date
        </label>
        <input
          id="ingest-bars-from-date"
          type="date"
          value={fromDate}
          onChange={(event) => setFromDate(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <div>
        <label
          htmlFor="ingest-bars-to-date"
          className="block text-xs text-zinc-500"
        >
          To date
        </label>
        <input
          id="ingest-bars-to-date"
          type="date"
          value={toDate}
          onChange={(event) => setToDate(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <div>
        <label
          htmlFor="ingest-bars-symbols"
          className="block text-xs text-zinc-500"
        >
          Symbols (comma-separated)
        </label>
        <input
          id="ingest-bars-symbols"
          type="text"
          value={symbolsText}
          onChange={(event) => setSymbolsText(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
        {symbols.length === 0 ? (
          <p className="mt-1 text-xs text-zinc-400">{SYMBOLS_EMPTY_HELP}</p>
        ) : null}
      </div>

      <JobFormFooter
        capability={capability}
        outcome={outcome}
        canSubmit={canSubmit}
        submitting={submitting}
        label="Submit Ingest Bars"
        onSubmit={handleSubmit}
      />
    </div>
  );
}
