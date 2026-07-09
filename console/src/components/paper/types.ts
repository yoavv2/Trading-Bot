// Shared types for the Paper Trading Status screen (PAPR-03/PAPR-04).
// Verified against the analytics response shape produced by
// src/trading_platform/services/analytics.py (_serialize_account_snapshot,
// _serialize_reconciliation) and consumed elsewhere by
// console/src/components/runs/detail/MetricsPanel.tsx.

export type AccountSnapshot = {
  snapshot_at: string; // ISO
  snapshot_source: string;
  cash: number; // _decimal_value -> float
  gross_exposure: number;
  total_equity: number; // PAPR-04 "equity"
  buying_power: number; // PAPR-04 "buying power"
  open_positions: number;
};

export type Reconciliation = {
  run_id: string;
  status: string;
  as_of_session: string | null;
  finding_count: number;
  blocking_count: number;
  blocks_execution: boolean; // PAPR-03 — the headline flag
  completed_at: string | null;
};

export type ExecutionFinding = {
  event_type: string;
  severity: string;
  blocks_execution: boolean;
  event_at: string; // ISO
  message: string;
  details: unknown;
};

export type PaperBlock = {
  latest_account_snapshot: AccountSnapshot | null; // PAPR-04 — MAY BE null
  latest_reconciliation: Reconciliation | null; // PAPR-03 — MAY BE null
  recent_execution_findings: ExecutionFinding[]; // strategy-wide, most-recent N — NOT scoped to the reconciliation run
};

export type AnalyticsResponse = {
  strategy: Record<string, unknown>;
  backtest: unknown | null;
  paper: PaperBlock | null; // whole block MAY be null when no strategy/paper history exists
};
