"use client";

import { useRef, useState } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { submitJob } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/idempotencyKey";
import type { JobTypeCatalogItem } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

type StrategyListItem = {
  strategy_id: string;
  display_name: string;
};

type StrategiesResponse = {
  count: number;
  strategies: StrategyListItem[];
};

// Structurally identical to jobTypeForms.ts's JobSubmissionFormProps --
// declared locally (rather than imported) so this file never references
// "jobTypeForms" (only NewJobView.tsx is permitted to import that map).
type BacktestJobFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

type SubmitOutcome =
  | { kind: "idle" }
  | { kind: "replayed" }
  | { kind: "error"; message: string };

/**
 * D-08/D-10/OPS-01: the `backtest` Job type's submission form (the sole
 * entry in lookup map (1)). All three fields (strategy_id, from_date,
 * to_date) are required -- D-08: the backend never defaults them inside
 * validate_payload, so the Job payload records exactly what will run.
 * from_date/to_date pre-fill from the catalog's submission_defaults when
 * present, else stay empty; never computed client-side. Submits with one
 * Idempotency-Key per form instance, reused across an identical-payload
 * retry (e.g. a network failure) and rotated the moment the payload
 * changes, so a transport-failure retry never risks a false
 * idempotency_key_conflict (T-19-11-02).
 */
export function BacktestJobForm({
  catalogEntry,
  capability,
  initialParams,
  onNavigate,
}: BacktestJobFormProps) {
  const { result: strategiesResult } = useApiQuery<StrategiesResponse>(
    "/api/v1/strategies",
  );

  const [strategyId, setStrategyId] = useState(
    initialParams.strategy_id ?? "",
  );
  const [fromDate, setFromDate] = useState(
    catalogEntry?.submission_defaults?.from_date ?? "",
  );
  const [toDate, setToDate] = useState(
    catalogEntry?.submission_defaults?.to_date ?? "",
  );
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<SubmitOutcome>({ kind: "idle" });

  // One Idempotency-Key per form instance (T-19-11-02); rotated only when
  // the attempted payload changes from the previous attempt.
  // Generated lazily on first submit, never during render (WR-C-08).
  const keyRef = useRef<string | null>(null);
  const lastAttemptRef = useRef<string | null>(null);

  const strategies = strategiesResult?.ok
    ? strategiesResult.data.strategies
    : [];

  const canSubmit =
    strategyId.trim().length > 0 &&
    fromDate.length > 0 &&
    toDate.length > 0 &&
    !submitting &&
    capability.state === "enabled";

  async function handleSubmit() {
    if (!canSubmit) {
      return;
    }

    const payload = {
      strategy_id: strategyId,
      from_date: fromDate,
      to_date: toDate,
    };
    const payloadString = JSON.stringify(payload);
    if (
      keyRef.current === null ||
      (lastAttemptRef.current !== null &&
        lastAttemptRef.current !== payloadString)
    ) {
      keyRef.current = newIdempotencyKey();
    }
    const idempotencyKey = keyRef.current;
    lastAttemptRef.current = payloadString;

    setSubmitting(true);
    const result = await submitJob(
      { job_type: "backtest", payload },
      idempotencyKey,
    );
    setSubmitting(false);

    if (result.ok) {
      setOutcome(result.replayed ? { kind: "replayed" } : { kind: "idle" });
      onNavigate(`/jobs/${result.data.job_id}`);
      return;
    }
    setOutcome({ kind: "error", message: result.message });
  }

  return (
    <div className="max-w-md space-y-4">
      <div>
        <label
          htmlFor="backtest-strategy-id"
          className="block text-xs text-zinc-500"
        >
          Strategy
        </label>
        <select
          id="backtest-strategy-id"
          value={strategyId}
          onChange={(event) => setStrategyId(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        >
          <option value="">Select a strategy…</option>
          {strategies.map((strategy) => (
            <option key={strategy.strategy_id} value={strategy.strategy_id}>
              {strategy.display_name} ({strategy.strategy_id})
            </option>
          ))}
        </select>
      </div>

      <div>
        <label
          htmlFor="backtest-from-date"
          className="block text-xs text-zinc-500"
        >
          From date
        </label>
        <input
          id="backtest-from-date"
          type="date"
          value={fromDate}
          onChange={(event) => setFromDate(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      <div>
        <label
          htmlFor="backtest-to-date"
          className="block text-xs text-zinc-500"
        >
          To date
        </label>
        <input
          id="backtest-to-date"
          type="date"
          value={toDate}
          onChange={(event) => setToDate(event.target.value)}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
        />
      </div>

      {capability.state !== "enabled" && capability.reason ? (
        <p className="text-xs text-zinc-500">{capability.reason}</p>
      ) : null}

      {outcome.kind === "replayed" ? (
        <p className="text-xs text-zinc-400">
          Already submitted — opening existing Job
        </p>
      ) : null}

      {outcome.kind === "error" ? (
        <p className="text-xs text-red-400">{outcome.message}</p>
      ) : null}

      <button
        type="button"
        onClick={() => void handleSubmit()}
        disabled={!canSubmit}
        className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-300 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Submit Backtest
      </button>
    </div>
  );
}
