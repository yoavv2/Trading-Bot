import { isTerminalJobStatus } from "@/lib/jobStatus";

type JobResultSummaryPanelProps = {
  resultSummary: Record<string, unknown> | null;
  status: string;
  resourceCount: number;
};

function emptyCopy(status: string, resourceCount: number): string {
  if (status === "cancelled") {
    return resourceCount > 0
      ? "No result summary for this Job — see Linked resources below for the run's actual outcome."
      : "No result summary — this Job was cancelled before it produced a result.";
  }
  if (status === "failed") {
    return "No result summary — this Job failed; see the failure reason above.";
  }
  if (!isTerminalJobStatus(status)) {
    return "No result summary yet.";
  }
  return "No result summary yet.";
}

/**
 * JOBUI-02/D-06: renders result_summary generically as a key/value list --
 * every entry is rendered the same way (primitives via String(), nested
 * objects/arrays via JSON.stringify()) with no dict key ever special-cased.
 * The only authoritative navigable link for a produced resource is the
 * Linked resources panel (resources[]); this panel never derives a link
 * from a key inside this handler-authored, otherwise-untrusted dict.
 */
export function JobResultSummaryPanel({
  resultSummary,
  status,
  resourceCount,
}: JobResultSummaryPanelProps) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <h2 className="text-sm font-semibold text-zinc-200">Result summary</h2>
      <div className="mt-3 text-sm">
        {resultSummary === null ? (
          <p className="text-zinc-500">{emptyCopy(status, resourceCount)}</p>
        ) : (
          <dl className="grid grid-cols-[minmax(0,auto)_1fr] gap-x-4 gap-y-1 text-xs">
            {Object.entries(resultSummary).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="font-mono text-zinc-500">{key}</dt>
                <dd className="font-mono text-zinc-200">
                  {typeof value === "object" && value !== null
                    ? JSON.stringify(value)
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}
