"use client";

import { useState } from "react";
import {
  JobFormFooter,
  StrategySelectField,
  useJobFormSubmission,
} from "./jobFormKit";
import type { JobTypeCatalogItem } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

// Structurally identical to the job-type lookup map's JobSubmissionFormProps
// -- declared locally (rather than imported) so this file never references
// the job-type lookup map module (only NewJobView.tsx is permitted to
// import it).
type PaperSessionJobFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

/**
 * D-01/D-03/D-21/D-22/D-23: the `paper-session` Job type's submission form
 * -- the only broker-submission path after Phase 20 (D-28). `strategy_id`
 * and `as_of_session` are required, matching every other Phase 20 form;
 * `risk_run_id` is the one OPTIONAL field across the whole Phase 20 form
 * set (D-23) -- left blank, the service picks the latest succeeded risk
 * evaluation at run time. The payload always includes the `risk_run_id`
 * key (`null` when blank, the trimmed string otherwise) -- it is never
 * omitted, matching the backend's strict `extra="forbid"` schema which
 * requires the key present regardless of value. Composes the shared
 * jobFormKit mechanics (Plan 06) rather than duplicating
 * BacktestJobForm's submission logic. `paper-session` is cancellable only
 * while queued (D-01) -- once running, the session runs to completion --
 * but that is purely a server-side cancel-time concern; this submission
 * form is unaffected.
 */
export function PaperSessionJobForm({
  catalogEntry,
  capability,
  initialParams,
  onNavigate,
}: PaperSessionJobFormProps) {
  const [strategyId, setStrategyId] = useState(
    initialParams.strategy_id ?? "",
  );
  const [asOfSession, setAsOfSession] = useState(
    catalogEntry?.submission_defaults?.as_of_session ?? "",
  );
  const [riskRunId, setRiskRunId] = useState("");

  const { submitting, outcome, submit } = useJobFormSubmission({
    jobType: "paper-session",
    onNavigate,
  });

  const canSubmit =
    strategyId.trim().length > 0 &&
    asOfSession.length > 0 &&
    capability.state === "enabled";

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }
    const trimmedRiskRunId = riskRunId.trim();
    void submit({
      strategy_id: strategyId,
      as_of_session: asOfSession,
      risk_run_id: trimmedRiskRunId.length > 0 ? trimmedRiskRunId : null,
    });
  }

  return (
    <div className="max-w-md space-y-4">
      <StrategySelectField
        id="paper-session-strategy-id"
        value={strategyId}
        onChange={setStrategyId}
      />

      <div>
        <label
          htmlFor="paper-session-as-of-session"
          className="block text-xs text-zinc-500"
        >
          As of session
        </label>
        <input
          id="paper-session-as-of-session"
          type="date"
          value={asOfSession}
          onChange={(event) => setAsOfSession(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <div>
        <label
          htmlFor="paper-session-risk-run-id"
          className="block text-xs text-zinc-500"
        >
          Risk run ID (optional)
        </label>
        <input
          id="paper-session-risk-run-id"
          type="text"
          value={riskRunId}
          onChange={(event) => setRiskRunId(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
        <p className="mt-1 text-xs text-zinc-400">
          Leave blank to use the latest succeeded risk evaluation.
        </p>
      </div>

      <JobFormFooter
        capability={capability}
        outcome={outcome}
        canSubmit={canSubmit}
        submitting={submitting}
        label="Submit Paper Session"
        onSubmit={handleSubmit}
      />
    </div>
  );
}
