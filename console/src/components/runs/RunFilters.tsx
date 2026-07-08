"use client";

// RUNS-02: exact server-side enum values accepted by GET /api/v1/runs's
// run_type / status query params. Kept in sync with the runs read-surface
// RunType / RunStatus enums — do not add client-only values here.
const RUN_TYPES = [
  "dry_bootstrap",
  "backtest",
  "risk_evaluation",
  "paper_execution",
  "reconciliation",
  "operator_control",
] as const;

const RUN_STATUSES = ["pending", "running", "succeeded", "failed"] as const;

export type RunFiltersValue = {
  runType: string;
  status: string;
};

type RunFiltersProps = RunFiltersValue & {
  onChange: (next: RunFiltersValue) => void;
};

/**
 * Presentational, controlled filter bar for the Runs screen (RUNS-02). Does not
 * fetch — the parent page owns `runType`/`status` state and turns a change here
 * into `run_type=`/`status=` query params on the `/api/v1/runs` request, so
 * filtering happens server-side.
 */
export function RunFilters({ runType, status, onChange }: RunFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <label className="flex items-center gap-2 text-zinc-300">
        <span className="text-xs text-zinc-500">Run type</span>
        <select
          value={runType}
          onChange={(event) =>
            onChange({ runType: event.target.value, status })
          }
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-100"
        >
          <option value="">All</option>
          {RUN_TYPES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-zinc-300">
        <span className="text-xs text-zinc-500">Status</span>
        <select
          value={status}
          onChange={(event) =>
            onChange({ runType, status: event.target.value })
          }
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-100"
        >
          <option value="">All</option>
          {RUN_STATUSES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
