"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { StatusPanel } from "./StatusPanel";

type HealthData = {
  status: string;
  service: string;
  version: string;
  timestamp: string;
};

/** STAT-01: liveness — status, service slug, version from GET /health. */
export function HealthPanel() {
  const { loading, result, refetch } = useApiQuery<HealthData>("/health");

  return (
    <StatusPanel
      title="Health"
      loading={loading}
      result={result}
      refetch={refetch}
    >
      {(data) => (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-zinc-500">Status</dt>
          <dd
            className={
              data.status === "ok" ? "text-emerald-400" : "text-amber-400"
            }
          >
            {data.status}
          </dd>
          <dt className="text-zinc-500">Service</dt>
          <dd className="text-zinc-200">{data.service}</dd>
          <dt className="text-zinc-500">Version</dt>
          <dd className="text-zinc-200">{data.version}</dd>
        </dl>
      )}
    </StatusPanel>
  );
}
