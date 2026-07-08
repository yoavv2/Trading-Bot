"use client";

import type { ApiFailure } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { StatusPanel } from "./StatusPanel";

type Check = {
  status: string;
  detail?: string | null;
  required?: boolean;
};

type ReadyData = {
  status: "ready" | "degraded" | "starting" | string;
  ready: boolean;
  started_at: string | null;
  timestamp: string;
  checks: {
    application: Check;
    configuration: Check;
    database: Check;
  };
};

function checkColor(status: string): string {
  if (status === "ok") return "text-emerald-400";
  if (status === "error") return "text-red-400";
  return "text-zinc-400"; // skipped / starting
}

function ReadyChecks({ data }: { data: ReadyData }) {
  const rows: [string, Check][] = [
    ["Application", data.checks.application],
    ["Configuration", data.checks.configuration],
    ["Database", data.checks.database],
  ];

  return (
    <div className="space-y-2 text-sm">
      <p className="font-semibold text-zinc-200">
        {data.status}
        {data.ready ? "" : " (not ready)"}
      </p>
      <table className="w-full text-left text-xs">
        <tbody>
          {rows.map(([label, check]) => (
            <tr key={label} className="border-t border-zinc-800">
              <td className="py-1 pr-2 align-top text-zinc-500">{label}</td>
              <td
                className={`py-1 pr-2 align-top ${checkColor(check.status)}`}
              >
                {check.status}
                {check.required === false ? " (optional)" : ""}
              </td>
              <td className="py-1 align-top text-zinc-400">
                {check.detail ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function isReadyData(body: unknown): body is ReadyData {
  return (
    !!body &&
    typeof body === "object" &&
    "checks" in body &&
    typeof (body as { checks?: unknown }).checks === "object" &&
    (body as { checks?: unknown }).checks !== null
  );
}

/**
 * DB-connection-state surface (STAT-01): GET /ready returns 200 when ready but 503
 * when degraded/starting — both carry the same checks payload. A 503 here is DATA,
 * not just an error, so this panel renders the degraded checks from the failure body
 * instead of a bare ErrorState, falling back to ErrorState only when the body is
 * absent or unparseable.
 */
function renderReadinessError(failure: ApiFailure) {
  if (failure.status === 503 && isReadyData(failure.body)) {
    return (
      <div>
        <p className="mb-2 text-xs font-semibold text-amber-400">
          degraded — HTTP 503
        </p>
        <ReadyChecks data={failure.body} />
      </div>
    );
  }
  return <ErrorState failure={failure} />;
}

export function ReadinessPanel() {
  const { loading, result, refetch } = useApiQuery<ReadyData>("/ready");

  return (
    <StatusPanel
      title="Readiness"
      loading={loading}
      result={result}
      refetch={refetch}
      renderError={renderReadinessError}
    >
      {(data) => <ReadyChecks data={data} />}
    </StatusPanel>
  );
}
