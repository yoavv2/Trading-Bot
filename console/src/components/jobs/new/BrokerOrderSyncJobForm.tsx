"use client";

import { useState } from "react";
import { JobFormFooter, useJobFormSubmission } from "./jobFormKit";
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
 * D-01/D-21/D-22/D-25 (20.1-14, COMPAT-01): the `broker-order-sync` Job type's submission form.
 * It submits ACCOUNT scope only: the payload is exactly `{scope: "account"}`, plus
 * `as_of_session` only when the operator enters a date (blank by default, never pre-filled).
 * Account scope forbids strategy_id on the server, so there is no strategy field and a
 * `strategy_id` deep-link param is ignored; strategy-scope submission stays API-only.
 * Composes the shared jobFormKit mechanics (Idempotency-Key per opening, error mapping,
 * capability gating).
 */
export function BrokerOrderSyncJobForm({
  capability,
  onNavigate,
}: BrokerOrderSyncJobFormProps) {
  const [asOfSession, setAsOfSession] = useState("");

  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "broker-order-sync",
    onNavigate,
  });

  const canSubmit = capability.state === "enabled";

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    void submit(
      asOfSession.length > 0
        ? { scope: "account", as_of_session: asOfSession }
        : { scope: "account" },
    );
  }

  return (
    <div className="max-w-md space-y-4">
      <div>
        <label
          htmlFor="broker-order-sync-as-of-session"
          className="block text-xs text-zinc-500"
        >
          As of session (optional)
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
