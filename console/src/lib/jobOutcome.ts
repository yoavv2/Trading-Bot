// 20.1-14 (COMPAT-01, D-31, 05 section 4): catalog lookup, the api_only test and the Job
// Outcome view shown next to a Job's lifecycle status.
//
// D-17: components/jobs/** are job-type-agnostic and may not compare `job_type`. Every
// catalog lookup by job type, the api_only test and the outcome label mapping therefore live
// HERE (outside the scan) and are handed `job.job_type` as an argument.

import type {
  JobOutcomeValue,
  JobTypeCatalogItem,
  JobTypesCatalog,
} from "../components/jobs/types";

/** One copy for the api_only notice and the Retry notice. */
export const API_ONLY_NOTICE = "Operated through the API in this version";

/** The fallback label when a Job of an api_only type has no derivable Outcome. */
export const OUTCOME_VIA_API_ONLY = "Outcome via API only";

/** The only job_type equality in the console Job path. */
export function catalogEntryFor(
  catalog: JobTypesCatalog | null | undefined,
  jobType: string,
): JobTypeCatalogItem | undefined {
  return catalog?.items.find((item) => item.job_type === jobType);
}

export function isApiOnly(entry: JobTypeCatalogItem | null | undefined): boolean {
  return entry?.console_submission === "api_only";
}

export type OutcomeTone = "success" | "warning" | "neutral" | "danger";

export type OutcomeView = { label: string; tone: OutcomeTone };

// Closed reason labels (operation pause / re-evaluation / termination reasons and the
// blocked / no-action session reasons). Unknown reasons fall back to the raw value.
const REASON_LABELS: Readonly<Record<string, string>> = {
  working_order_commitments_unaccounted: "working order",
  awaiting_reconciliation: "awaiting reconciliation",
  kill_switch_tripped: "kill switch tripped",
  strategy_disabled: "strategy disabled",
  reconciliation_blocking: "reconciliation blocking",
  unrecognized_broker_activity: "unrecognized broker activity",
  outcome_unresolved: "outcome unresolved",
  broker_unavailable: "broker unavailable",
  execution_window_not_open: "execution window not open",
  price_unavailable: "price unavailable",
  not_active_paper_strategy: "not the active paper strategy",
  price_moved_beyond_tolerance: "price moved beyond tolerance",
  evaluation_data_changed: "evaluation data changed",
  strategy_settings_changed: "strategy settings changed",
  execution_window_elapsed: "execution window elapsed",
  cancelled_by_operator: "cancelled by operator",
  evaluation_superseded: "evaluation superseded",
  global_kill_switch: "kill switch tripped",
  mid_run_global_kill_switch: "kill switch tripped",
  mid_run_not_active_paper_strategy: "not the active paper strategy",
  reconciliation: "reconciliation blocking",
  evaluation_basis_unverified: "evaluation basis unverified",
  no_candidates: "no candidates",
  existing_orders: "orders already exist",
};

export function reasonLabel(reason: string | null | undefined): string | null {
  if (!reason) {
    return null;
  }
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

type OutcomeJob = {
  status: string;
  outcome?: JobOutcomeValue | null;
  outcome_reason?: string | null;
  outcome_detail?: { failed_count?: number } | null;
};

function withReason(prefix: string, reason: string | null | undefined): string {
  const label = reasonLabel(reason);
  return label ? `${prefix}: ${label}` : prefix;
}

/**
 * The Outcome view of a SUCCEEDED Job, or `null` for any other lifecycle status (they render
 * their lifecycle status unchanged). Success (emerald) styling is returned ONLY for `complete`
 * and for Jobs with no outcome semantics; every non-final or non-complete outcome and the
 * api_only fallback are never `success`.
 */
export function outcomeView(
  job: OutcomeJob,
  entry: JobTypeCatalogItem | null | undefined,
): OutcomeView | null {
  if (job.status !== "succeeded") {
    return null;
  }
  switch (job.outcome) {
    case "complete":
      return { label: "Succeeded · Complete", tone: "success" };
    case "partial": {
      const count = job.outcome_detail?.failed_count;
      const suffix =
        typeof count === "number"
          ? ` (${count} ${count === 1 ? "symbol" : "symbols"} failed)`
          : "";
      return { label: `Succeeded · Partial${suffix}`, tone: "warning" };
    }
    case "paused":
      return {
        label: `Succeeded · ${withReason("Paused", job.outcome_reason)}`,
        tone: "warning",
      };
    case "requires_reevaluation":
      return {
        label: `Succeeded · ${withReason("Re-evaluation required", job.outcome_reason)}`,
        tone: "warning",
      };
    case "terminated":
      return {
        label: `Succeeded · ${withReason("Ended", job.outcome_reason)}`,
        tone: "warning",
      };
    case "blocked":
      return {
        label: `Succeeded · ${withReason("Blocked", job.outcome_reason)}`,
        tone: "warning",
      };
    case "no_action":
      return { label: "Succeeded · No action", tone: "neutral" };
    case "failed":
      return { label: "Succeeded · Failed", tone: "danger" };
    default:
      break;
  }
  // No outcome: an api_only type must never read as success; everything else keeps today's
  // plain success rendering.
  if (isApiOnly(entry)) {
    return { label: OUTCOME_VIA_API_ONLY, tone: "neutral" };
  }
  return { label: "Succeeded", tone: "success" };
}

const TONE_CLASS: Readonly<Record<OutcomeTone, string>> = {
  success: "text-emerald-400",
  warning: "text-amber-400",
  neutral: "text-zinc-400",
  danger: "text-red-400",
};

/** Text color class of an outcome badge (emerald only for `success`). */
export function outcomeToneClass(tone: OutcomeTone): string {
  return TONE_CLASS[tone];
}
