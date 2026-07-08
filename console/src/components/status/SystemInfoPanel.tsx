"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { StatusPanel } from "./StatusPanel";

type SystemData = {
  application: {
    name: string;
    version: string;
    environment: string;
    operator_mode: string;
  };
  database: {
    driver: string;
    host: string;
    port: number;
    name: string;
    readiness_checks_enabled: boolean;
    readiness_required: boolean;
    schema_managed_by: string;
  };
};

/** STAT-01: environment name plus DB connection target from GET /api/v1/system. */
export function SystemInfoPanel() {
  const { loading, result, refetch } = useApiQuery<SystemData>(
    "/api/v1/system",
  );

  return (
    <StatusPanel
      title="System Info"
      loading={loading}
      result={result}
      refetch={refetch}
    >
      {(data) => (
        <div className="space-y-3 text-sm">
          <div>
            <p className="text-xs uppercase tracking-wide text-zinc-500">
              Environment
            </p>
            <p className="text-lg font-bold text-zinc-100">
              {data.application.environment}
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
            <dt className="text-zinc-500">Application</dt>
            <dd className="text-zinc-200">
              {data.application.name} v{data.application.version}
            </dd>
            <dt className="text-zinc-500">Operator mode</dt>
            <dd className="text-zinc-200">{data.application.operator_mode}</dd>
            <dt className="text-zinc-500">DB driver</dt>
            <dd className="text-zinc-200">{data.database.driver}</dd>
            <dt className="text-zinc-500">DB host</dt>
            <dd className="text-zinc-200">
              {data.database.host}:{data.database.port}
            </dd>
            <dt className="text-zinc-500">DB name</dt>
            <dd className="text-zinc-200">{data.database.name}</dd>
            <dt className="text-zinc-500">Schema managed by</dt>
            <dd className="text-zinc-200">{data.database.schema_managed_by}</dd>
          </dl>
        </div>
      )}
    </StatusPanel>
  );
}
