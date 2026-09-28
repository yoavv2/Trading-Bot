"use client";

import { useApiQuery, type QueryState } from "@/lib/useApiQuery";
import type { StrategyControlState } from "@/lib/api";
import { useControlChanged } from "./controlEvents";

/**
 * D-13/Amendment 2026-09-28: reads the effective DB control status via
 * `GET /api/v1/controls/strategies/{id}` — deliberately NOT
 * `GET /api/v1/strategies/{id}`'s `enabled` field, which is
 * `StrategyMetadata.enabled` loaded once from `config/strategies/*.yaml`
 * and never changes when an operator enables/disables the strategy. The
 * control-state read is the only honest source for a confirmation
 * dialog's current state. Refetches whenever any mounted display's
 * strategy control mutation resolves (`strategy:changed`), so every
 * simultaneously-mounted display of this domain's state stays in sync
 * within the tab.
 */
export function useStrategyControlState(
  strategyId: string,
): QueryState<StrategyControlState> {
  const query = useApiQuery<StrategyControlState>(
    `/api/v1/controls/strategies/${encodeURIComponent(strategyId)}`,
  );
  useControlChanged("strategy", query.refetch);
  return query;
}
