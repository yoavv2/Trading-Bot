"use client";

import { useState } from "react";
import { RunFilters, type RunFiltersValue } from "@/components/runs/RunFilters";
import { RunsTable } from "@/components/runs/RunsTable";

/**
 * Runs screen (RUNS-01/RUNS-02): filterable table over GET /api/v1/runs
 * spanning all run types, with server-side run_type/status filtering and
 * per-row drill-down into /runs/{run_id} (route added in 14-03).
 */
export default function RunsPage() {
  const [filters, setFilters] = useState<RunFiltersValue>({
    runType: "",
    status: "",
  });

  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">Runs</h1>
      <div className="space-y-4">
        <RunFilters
          runType={filters.runType}
          status={filters.status}
          onChange={setFilters}
        />
        <RunsTable runType={filters.runType} status={filters.status} />
      </div>
    </main>
  );
}
