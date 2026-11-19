import Link from "next/link";
import type { Progress } from "@/lib/research/types";
import { jobStatusColor, JOB_STATUS_BADGE_CLASS } from "@/lib/jobStatus";

/**
 * The revision's Job graph by role, each row linking to the generic Job detail page
 * (progress, logs, events, cancel live there). Nothing here decides anything.
 */
export function ProgressPanel({ progress }: { progress: Progress }) {
  return (
    <section className="surface-panel rounded-lg p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="eyebrow">Execution activity</p>
          <h3 className="mt-1 text-sm font-semibold text-foreground">Evaluation jobs</h3>
        </div>
        <span className="badge">{progress.count} total</span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {progress.count} Job(s):{" "}
        {Object.entries(progress.by_status)
          .map(([status, count]) => `${count} ${status}`)
          .join(", ") || "none submitted"}
      </p>
      {progress.jobs.length > 0 ? (
        <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="surface-muted text-left text-xs uppercase text-muted-foreground">
            <tr>
              <th className="py-1">Role</th>
              <th>Scope</th>
              <th>Detail</th>
              <th>Status</th>
              <th>Step</th>
              <th>Job</th>
            </tr>
          </thead>
          <tbody>
            {progress.jobs.map((job) => (
              <tr key={job.job_id} className="border-t border-border">
                <td className="py-1">{job.role}{job.is_rerun ? " (rerun)" : ""}</td>
                <td className="text-muted-foreground">{job.scope}</td>
                <td className="font-mono text-xs text-muted-foreground">
                  {[job.detail.asset, job.detail.window_role, job.detail.strategy_version_id ? String(job.detail.strategy_version_id).slice(0, 8) : null].filter(Boolean).join(" · ")}
                </td>
                <td>
                  <span className={`${JOB_STATUS_BADGE_CLASS} ${jobStatusColor(job.status)}`}>{job.status}</span>
                  {job.failure_message ? <span className="ml-2 text-xs text-negative">{job.failure_message.slice(0, 120)}</span> : null}
                </td>
                <td className="text-xs text-muted-foreground">{job.progress_step ?? ""}</td>
                <td>
                  <Link href={`/jobs/${job.job_id}`} className="font-mono text-xs text-accent hover:underline">
                    {job.job_id.slice(0, 8)}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : null}
    </section>
  );
}
