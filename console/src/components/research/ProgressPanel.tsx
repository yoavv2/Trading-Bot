import Link from "next/link";
import type { Progress } from "@/lib/research/types";
import { jobStatusColor, JOB_STATUS_BADGE_CLASS } from "@/lib/jobStatus";

/**
 * The revision's Job graph by role, each row linking to the generic Job detail page
 * (progress, logs, events, cancel live there). Nothing here decides anything.
 */
export function ProgressPanel({ progress }: { progress: Progress }) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <h3 className="text-sm font-semibold text-zinc-200">Jobs</h3>
      <p className="mt-1 text-xs text-zinc-500">
        {progress.count} Job(s):{" "}
        {Object.entries(progress.by_status)
          .map(([status, count]) => `${count} ${status}`)
          .join(", ") || "none submitted"}
      </p>
      {progress.jobs.length > 0 ? (
        <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
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
              <tr key={job.job_id} className="border-t border-zinc-800">
                <td className="py-1">{job.role}{job.is_rerun ? " (rerun)" : ""}</td>
                <td className="text-zinc-400">{job.scope}</td>
                <td className="font-mono text-xs text-zinc-400">
                  {[job.detail.asset, job.detail.window_role, job.detail.strategy_version_id ? String(job.detail.strategy_version_id).slice(0, 8) : null].filter(Boolean).join(" · ")}
                </td>
                <td>
                  <span className={`${JOB_STATUS_BADGE_CLASS} ${jobStatusColor(job.status)}`}>{job.status}</span>
                  {job.failure_message ? <span className="ml-2 text-xs text-red-200">{job.failure_message.slice(0, 120)}</span> : null}
                </td>
                <td className="text-xs text-zinc-400">{job.progress_step ?? ""}</td>
                <td>
                  <Link href={`/jobs/${job.job_id}`} className="font-mono text-xs text-cyan-300 hover:underline">
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
