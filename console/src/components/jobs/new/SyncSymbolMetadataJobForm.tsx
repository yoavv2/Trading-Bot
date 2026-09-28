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
type SyncSymbolMetadataJobFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

/**
 * OPS-05/D-25: the `sync-symbol-metadata` Job type's submission form. The
 * `symbols` field is required -- the backend never defaults it inside
 * validate_payload, so the Job payload records exactly what will run. It
 * pre-fills from the catalog's submission_defaults (the configured metadata
 * universe) when present, else stays empty. `symbols` is edited as a single
 * comma-separated text field but always submitted as a normalized
 * `string[]` (`parseSymbolsInput`, UI-SPEC symbols wire-shape note, same
 * widget as `ingest-bars`) -- never a string. Composes the shared
 * jobFormKit mechanics (Plan 06) rather than duplicating BacktestJobForm's
 * submission logic. No screen shortcut owns this Job type (UI-SPEC) --
 * `initialParams` is part of the shared form-props shape but unused here.
 */
export function SyncSymbolMetadataJobForm({
  catalogEntry,
  capability,
  onNavigate,
}: SyncSymbolMetadataJobFormProps) {
  const [symbolsText, setSymbolsText] = useState(
    catalogEntry?.submission_defaults?.symbols ?? "",
  );

  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "sync-symbol-metadata",
    onNavigate,
  });

  const symbols = parseSymbolsInput(symbolsText);
  const canSubmit = symbols.length > 0 && capability.state === "enabled";

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    void submit({ symbols });
  }

  return (
    <div className="max-w-md space-y-4">
      <div>
        <label
          htmlFor="sync-symbol-metadata-symbols"
          className="block text-xs text-zinc-500"
        >
          Symbols (comma-separated)
        </label>
        <input
          id="sync-symbol-metadata-symbols"
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
        label="Submit Sync Symbol Metadata"
        onSubmit={handleSubmit}
      />
    </div>
  );
}
