"use client";

import { useApiQuery } from "./useApiQuery";

const ACTIVE_PAPER_STRATEGY_ENDPOINT = "/api/v1/controls/active-paper-strategy";

// Closed `TradingBlocker` values of GET /api/v1/controls/active-paper-strategy
// `trading_blocked_reasons` (20.1-14; services/execution/permission.py). The labels are the
// operator wording; an unknown value renders its raw text with underscores replaced.
const TRADING_BLOCKER_LABELS: Readonly<Record<string, string>> = {
  no_active_paper_strategy: "no active paper strategy",
  strategy_disabled: "strategy disabled",
  kill_switch_tripped: "kill switch tripped",
  outcome_unresolved: "uncertain order outcome unresolved",
  reconciliation_blocking: "reconciliation blocking",
  unrecognized_broker_activity: "unrecognized broker activity",
  working_order_commitments_unaccounted: "working order commitments unaccounted",
};

export function tradingBlockerLabel(reason: string): string {
  return TRADING_BLOCKER_LABELS[reason] ?? reason.replace(/_/g, " ");
}

export type ActivePaperStrategySnapshot = {
  /** `known` only when the GET succeeded with a well-formed body; `unknown` covers a failed fetch. */
  state: "loading" | "known" | "unknown";
  strategyId: string | null;
  displayName: string | null;
  /**
   * The closed trading-blocked reasons; `null` whenever they are not known (loading, failed
   * fetch, or an older API without the field). An empty array means "known, nothing blocks".
   */
  tradingBlockedReasons: string[] | null;
};

type ActivePaperStrategyBody = {
  strategy_id?: string | null;
  display_name?: string | null;
  trading_blocked_reasons?: unknown;
};

/**
 * Read-only view of GET /api/v1/controls/active-paper-strategy (20.1-14, COMPAT-01). Issues a
 * GET only; the old console never changes the owner (that stays API-only in this version).
 */
export function useActivePaperStrategy(): ActivePaperStrategySnapshot {
  const { result } = useApiQuery<ActivePaperStrategyBody>(ACTIVE_PAPER_STRATEGY_ENDPOINT);

  if (!result) {
    return { state: "loading", strategyId: null, displayName: null, tradingBlockedReasons: null };
  }
  if (!result.ok) {
    return { state: "unknown", strategyId: null, displayName: null, tradingBlockedReasons: null };
  }
  const body = result.data;
  const ownerKnown =
    body !== null &&
    typeof body === "object" &&
    "strategy_id" in body &&
    (body.strategy_id === null || typeof body.strategy_id === "string");
  if (!ownerKnown) {
    return { state: "unknown", strategyId: null, displayName: null, tradingBlockedReasons: null };
  }
  const reasons = Array.isArray(body.trading_blocked_reasons)
    ? body.trading_blocked_reasons.filter((r): r is string => typeof r === "string")
    : null;
  return {
    state: "known",
    strategyId: body.strategy_id ?? null,
    displayName: typeof body.display_name === "string" ? body.display_name : null,
    tradingBlockedReasons: reasons,
  };
}
