// Read-model types of the research routes (S2-S4 backend contracts). Deep result
// documents (comparison.json) are typed where the components render them and kept
// as `unknown`-tolerant records elsewhere so an additive backend field never breaks a
// page.

export type Draft = {
  draft_id: string;
  title: string;
  yaml_text: string;
  source: string;
  parent_version_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type StrategyVersion = {
  version_id: string;
  strategy_id: string;
  version_no: number;
  name: string;
  spec_sha256: string;
  history_required: number;
  history_minimum: number;
  scale_class: string;
  source: string;
  parent_version_id: string | null;
  behaviour_differs_from_original: string | null;
  approved_at: string | null;
  yaml_text?: string;
  spec_json?: Record<string, unknown>;
  explanation?: string;
};

export type Family = { strategy_id: string; version_count: number; latest: StrategyVersion };

export type SpecError = { code: string; path: string; message: string };

export type ValidationOutcome = {
  valid: boolean;
  errors: SpecError[];
  derived: {
    name: string;
    spec_sha256: string;
    history_required: number;
    history_minimum: number;
    scale_class: string;
    terms_used: string[];
    operators_used: string[];
  } | null;
  explanation: string | null;
};

export type CatalogEntry = {
  ticker: string;
  name: string | null;
  name_source: string | null;
  exchange: string | null;
  asset_type: string | null;
  currency: string | null;
  catalog_start: string | null;
  catalog_end: string | null;
};

export type CatalogSearch = {
  query: string;
  count: number;
  items: CatalogEntry[];
  name_coverage: { rows_named: number; rows_total: number };
  name_search_note: string;
};

export type CatalogCoverage = {
  rows_total: number;
  rows_named: number;
  name_search_note: string;
  coverage_note: string;
  last_synced_at: string | null;
  max_assets_per_study: number;
};

export type AssetList = { list_id: string; name: string; tickers: string[]; updated_at: string | null };

export type Window = { start: string; end: string };

export type StudySettings = {
  mode: string;
  strategy_version_ids: string[];
  assets: string[];
  asset_list_id: string | null;
  range: Window;
  windows: { development: Window; validation: Window; final_test: Window };
  initial_capital: string;
  quantity_policy: string;
  costs: { slippage_bps: string; commission_per_order: string } | null;
  objective: string | null;
  constraint_value: string | null;
  provider: string;
  adjusted: boolean;
  note: string;
};

export type Revision = {
  revision_id: string;
  study_id: string;
  revision_no: number;
  settings: StudySettings;
  data_freeze_id: string | null;
  created_at: string | null;
  freeze?: Freeze | null;
  evaluations?: Record<string, { evaluation_id: string; created_at: string | null; is_rerun: boolean }>;
};

export type Study = { study_id: string; name: string; kind: string; created_at: string | null; revisions?: Revision[] };

export type ReadinessError = { code: string; item: string; [key: string]: unknown };

export type InputsState = "pending" | "failed" | "stale" | "verified";

export type Readiness = {
  revision_id: string;
  ready: boolean;
  status: string;
  preflight: { ready: boolean; errors: ReadinessError[] };
  inputs: {
    state: InputsState;
    verified: boolean;
    errors: ReadinessError[];
    attempt: { job_id: string; status: string; completed_at: string | null; failure_message: string | null; link: string } | null;
    attempts: number;
    data_freeze: { data_freeze_id: string; frozen_at: string | null; input_digest: string; inputs_path: string | null } | null;
    downstream: string;
  };
  errors: ReadinessError[];
  checked: { pairs: number; assets: number; strategy_versions: number; required_start_by_pair: Record<string, string | null>; pinned_calendar_start: string | null };
};

export type ProgressJob = {
  job_id: string;
  role: string;
  scope: string;
  is_rerun: boolean;
  detail: Record<string, unknown>;
  job_type: string;
  status: string;
  progress_step: string | null;
  failure_reason: string | null;
  failure_message: string | null;
  link: string;
};

export type Progress = { revision_id: string; count: number; by_status: Record<string, number>; jobs: ProgressJob[] };

export type Metrics = {
  net_total_return: number | null;
  cagr: number | null;
  max_drawdown: number | null;
  drawdown_duration: number;
  closed_trades: number;
  open_at_end: { count: number; unrealized_pnl: number | null };
  holding_period: { median_sessions: number | null; mean_sessions: number | null };
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  sharpe_daily_ann: number | null;
  sortino_daily_ann: number | null;
  exposure: number | null;
  turnover: number | null;
  total_costs: { currency: number | null; slippage: number | null; commission: number | null; pct_of_initial_capital: number | null };
  rounding_slack: number | null;
  skipped_fills: number;
  zero_quantity_fills: number;
  flags: string[];
  notes: Record<string, string>;
};

export type Evidence = {
  grade: string;
  grade_reasons: string[];
  descriptors: {
    closed_trades: number;
    open_at_end: number;
    measured_sessions: number;
    median_holding_sessions: number | null;
    holding_ratio: number | null;
    clusters: number;
    cluster_span_sessions: number;
    concentration: { best_trade_share: number | null; best_three_share: number | null; best_month_share: number | null };
  };
  limitations: string[];
};

export type CandidateWindow = {
  run_id: string;
  metrics: Metrics;
  evidence: Evidence;
  excess_return_vs_benchmark?: number | null;
};

export type Candidate = {
  strategy_version_id: string;
  strategy_name: string | null;
  version_no: number | null;
  asset: string;
  status: string;
  status_reasons: string[];
  rank: number | null;
  co_leading: boolean;
  windows: Record<string, CandidateWindow | null>;
  benchmark: Record<string, (Metrics & { run_id: string }) | null>;
};

export type RankingRow = {
  rank: number;
  strategy_version_id: string;
  asset: string;
  co_leading: boolean;
  net_total_return: number | null;
  max_drawdown: number | null;
  cagr: number | null;
  excess_return_vs_benchmark: number | null;
  grade: string;
};

export type FinalTestBlock = {
  state: string;
  note?: string;
  outcome?: string;
  outcome_reasons?: string[];
  thin_evidence?: boolean;
  acceptance?: { constraint_value: number; objective_minimum: number; objective?: string };
  benchmark_comparison?: {
    return_vs_benchmark: string | null;
    drawdown_vs_benchmark: string | null;
    excess_return_vs_benchmark: number | null;
    note: string;
  } | null;
  wording?: string;
  reproducibility?: { byte_identical: boolean; status: string } | null;
  candidate?: { strategy_version_id: string; asset: string; run_id: string | null; metrics: Metrics | null; evidence: Evidence | null };
  benchmark?: { run_id: string | null; metrics: Metrics | null };
  exposures?: Exposures;
  outside_visibility_limitation?: string;
  is_rerun?: boolean;
};

export type Comparison = {
  schema_version: number;
  study: { study_id: string; name: string; kind: string } | null;
  revision: { revision_id: string; revision_no: number };
  settings: StudySettings;
  mode_label: string;
  windows: Record<string, Window>;
  windows_evaluated: string[];
  data: { provider: string; adjusted: boolean; data_freeze_id: string | null; input_digest: string | null; calendar_start: string | null };
  code_sha: string;
  candidates: Candidate[];
  ranking: { objective: string; constraint_value: number; ranking_key: string; ranking: RankingRow[]; co_leaders: RankingRow[]; verdict: string; verdict_label: string; profit_factor_affects_order: boolean };
  verdict: string;
  verdict_label: string;
  benchmark_note: string;
  final_test: FinalTestBlock;
  limitations: string[];
  evidence_limitations: string[];
};

export type Freeze = {
  freeze_id: string;
  candidate: { strategy_version_id: string; asset: string };
  acceptance: { constraint_value: number; objective_minimum: number; objective?: string };
  frozen_at: string | null;
  co_leading_choice_reason: string | null;
};

export type Exposure = {
  exposure_id: string;
  asset: string;
  range: Window;
  study_id: string | null;
  revision_id: string | null;
  strategy_version_id: string | null;
  reason: string;
  is_rerun: boolean;
  state: "run_recorded" | "results_inspected";
  run_recorded_at: string | null;
  results_inspected_at: string | null;
  context: { study_name?: string | null; revision_no?: number | null; strategy_name?: string | null; version_no?: number | null; outcome?: string | null; same_revision?: boolean };
  link: string | null;
};

export type Exposures = {
  proposed_window: Window;
  assets: string[];
  count: number;
  by_state: Record<string, number>;
  items: Exposure[];
  limitation: string;
};

export type RunSummary = {
  run_id: string;
  status: string;
  strategy_version_id: string | null;
  benchmark: boolean;
  asset: string;
  window_role: string;
  rerun_of: string | null;
  spec_sha256: string | null;
  code_sha: string | null;
  input_digest: string | null;
  results_digest: string | null;
  metrics: Metrics | null;
  evidence: Evidence | null;
  link: string | null;
};

export type Results = {
  revision: Revision;
  runs: RunSummary[];
  initial: Comparison | null;
  final_test: FinalTestBlock;
  final_test_history: { evaluation_id: string; is_rerun: boolean; outcome: string | null; created_at: string | null }[];
};

export type CurvePoint = { session_date: string; total_equity: number; drawdown: number; gross_exposure: number };
export type Curve = { run_id: string; asset: string; window_role: string; benchmark: boolean; points: CurvePoint[] };
