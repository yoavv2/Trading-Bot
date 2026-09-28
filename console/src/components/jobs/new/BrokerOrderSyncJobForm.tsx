"use client";

import { useState } from "react";
import {
  JobFormFooter,
  StrategySelectField,
  useJobFormSubmission,
  useStrategySelection,
} from "./jobFormKit";
import type { JobTypeCatalogItem } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

// Structurally identical to the job-type lookup map's JobSubmissionFormProps
// -- declared locally (rather than imported) so this file never references
// the job-type lookup map module (only NewJobView.tsx is permitted to
// import it).
type BrokerOrderSyncJobFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

/**
 * D-01/D-19/D-21/D-22/D-25: the `broker-order-sync` Job type's submission
 * form. Both fields (strategy_id, as_of_session) are required -- the
 * backend never defaults them inside validate_payload, so the Job payload
 * records exactly what will run. as_of_session pre-fills from the
 * catalog's submission_defaults when present, else stays empty;
 * strategy_id pre-fills from initialParams (deep link), never computed
 * client-side. Composes the shared jobFormKit mechanics (Plan 06) rather
 * than duplicating BacktestJobForm's submission logic. `broker-order-sync`
 * is cancellable only while queued (D-01) and reconcile-first-blocked on
 * retry (D-19), but both are purely server-side concerns -- this
 * submission form is unaffected.
 */
export function BrokerOrderSyncJobForm({
  catalogEntry,
  capability,
  initialParams,
  onNavigate,
}: BrokerOrderSyncJobFormProps) {
  const { strategyId, setStrategyId, strategies, validStrategyId } =
    useStrategySelection(initialParams.strategy_id);
  const [asOfSession, setAsOfSession] = useState(
    catalogEntry?.submission_defaults?.as_of_session ?? "",
  );

  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "broker-order-sync",
    onNavigate,
  });

  const canSubmit =
    validStrategyId !== null &&
    asOfSession.length > 0 &&
    capability.state === "enabled";

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    void submit({ strategy_id: validStrategyId, as_of_session: asOfSession });
  }

  return (
    <div className="max-w-md space-y-4">
      <StrategySelectField
        id="broker-order-sync-strategy-id"
        value={strategyId}
        strategies={strategies}
        onChange={setStrategyId}
      />

      <div>
        <label
          htmlFor="broker-order-sync-as-of-session"
          className="block text-xs text-zinc-500"
        >
          As of session
        </label>
        <input
          id="broker-order-sync-as-of-session"
          type="date"
          value={asOfSession}
          onChange={(event) => setAsOfSession(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <JobFormFooter
        capability={capability}
        outcome={outcome}
        canSubmit={canSubmit}
        submitting={submitting}
        label="Submit Broker Order Sync"
        onSubmit={handleSubmit}
      />
    </div>
  );
}
