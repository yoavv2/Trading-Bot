"use client";

import { useRef, useState } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { submitJob } from "@/lib/api";
import { newIdempotencyKey } from "@/lib/idempotencyKey";
import type { MutationCapability } from "@/lib/useMutationCapability";

/**
 * Shared submission mechanics for the seven new-since-Phase-20 Job forms
 * (RiskEvaluationJobForm, PaperSessionJobForm, ReconciliationJobForm,
 * IngestBarsJobForm, SyncSymbolMetadataJobForm, SyncMarketSessionsJobForm,
 * BrokerOrderSyncJobForm), generalizing BacktestJobForm.tsx's existing
 * idempotency-key/submit/outcome logic verbatim so each new form is a thin
 * composition over this kit rather than a fifth-through-eighth copy of it.
 * Lives under components/jobs/new/, which is outside the D-17 job-type-
 * agnostic UI scope (see consoleBoundaries.test.ts), but this file itself
 * never imports the job_type -> form lookup map -- it stays a generic
 * kit parameterized by a caller-supplied jobType string.
 */

export type JobFormOutcome =
  | { kind: "idle" }
  | { kind: "replayed" }
  | { kind: "error"; message: string };

/**
 * One Idempotency-Key per hook instance (mirrors BacktestJobForm's
 * T-19-11-02 mitigation), rotated only when the attempted payload's
 * JSON.stringify differs from the previous attempt -- so a transport
 * failure retry of an unchanged payload reuses the key, and a changed
 * payload never risks a false idempotency_key_conflict.
 */
export function useJobFormSubmission({
  jobType,
  onNavigate,
}: {
  jobType: string;
  onNavigate: (href: string) => void;
}): {
  submitting: boolean;
  outcome: JobFormOutcome;
  submit: (payload: Record<string, unknown>) => Promise<void>;
} {
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<JobFormOutcome>({ kind: "idle" });

  // Generated lazily on first submit, never during render (WR-C-08): a
  // render-time call re-evaluates on every render and, on insecure contexts
  // without crypto.randomUUID, would throw and unmount the page.
  const keyRef = useRef<string | null>(null);
  const lastAttemptRef = useRef<string | null>(null);

  async function submit(payload: Record<string, unknown>) {
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
    const result = await submitJob({ job_type: jobType, payload }, idempotencyKey);
    setSubmitting(false);

    if (result.ok) {
      setOutcome(result.replayed ? { kind: "replayed" } : { kind: "idle" });
      onNavigate(`/jobs/${result.data.job_id}`);
      return;
    }
    setOutcome({ kind: "error", message: result.message });
  }

  return { submitting, outcome, submit };
}

/**
 * Shared footer markup (reason / replay / error copy + the primary submit
 * button), byte-for-byte the same classes BacktestJobForm.tsx uses today.
 * `label` supplies the button's text (e.g. "Submit Risk Evaluation").
 */
export function JobFormFooter({
  capability,
  outcome,
  canSubmit,
  submitting,
  label,
  onSubmit,
}: {
  capability: MutationCapability;
  outcome: JobFormOutcome;
  canSubmit: boolean;
  submitting: boolean;
  label: string;
  onSubmit: () => void;
}) {
  return (
    <>
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
        onClick={onSubmit}
        disabled={!canSubmit || submitting}
        className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-300 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {label}
      </button>
    </>
  );
}

type StrategyListItem = {
  strategy_id: string;
  display_name: string;
};

type StrategiesResponse = {
  count: number;
  strategies: StrategyListItem[];
};

const NO_STRATEGIES: StrategyListItem[] = [];

/**
 * Strategy selection state shared by the four strategy-bearing forms
 * (WR-C-07). `strategy_id` is seeded from the arbitrary `?strategy_id=`
 * deep-link text (trimmed), and the strategies list is fetched once here.
 * `validStrategyId` is the selected id only when it is exactly one of the
 * loaded options, else null -- forms must gate `canSubmit` on it and submit
 * it (never the raw state), so the select can never display the placeholder
 * while the form submits a hidden, unverified id. While the list is loading
 * or failed, the seed is retained (the deep link is not lost) but is not
 * submittable.
 */
export function useStrategySelection(seed: string | undefined): {
  strategyId: string;
  setStrategyId: (value: string) => void;
  strategies: StrategyListItem[];
  validStrategyId: string | null;
} {
  const { result } = useApiQuery<StrategiesResponse>("/api/v1/strategies");
  const [strategyId, setStrategyId] = useState((seed ?? "").trim());
  const strategies = result?.ok ? result.data.strategies : NO_STRATEGIES;
  const validStrategyId = strategies.some(
    (strategy) => strategy.strategy_id === strategyId,
  )
    ? strategyId
    : null;
  return { strategyId, setStrategyId, strategies, validStrategyId };
}

/**
 * Shared Strategy <select>, same classes/option markup as
 * BacktestJobForm.tsx, parameterized by the field id so each new form can
 * namespace its own label `htmlFor`. Presentational: the options and the
 * value come from `useStrategySelection`.
 */
export function StrategySelectField({
  id,
  value,
  strategies,
  onChange,
}: {
  id: string;
  value: string;
  strategies: StrategyListItem[];
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-zinc-500">
        Strategy
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
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
  );
}

/** Empty-normalized-list helper copy (ingest-bars/sync-symbol-metadata forms). */
export const SYMBOLS_EMPTY_HELP = "At least one symbol is required";

/**
 * Normalizes a comma-separated `symbols` text field into the wire shape
 * (D-24/D-25): split on ",", trim, upper-case, drop empties, de-duplicate,
 * sort. `parseSymbolsInput(" spy, aapl ,SPY,, ")` -> `["AAPL", "SPY"]`.
 */
export function parseSymbolsInput(text: string): string[] {
  const symbols = text
    .split(",")
    .map((entry) => entry.trim().toUpperCase())
    .filter((entry) => entry.length > 0);
  return Array.from(new Set(symbols)).sort();
}
