import type { JobProgress } from "../types";

type JobProgressPanelProps = {
  progress: JobProgress;
  status: string;
};

/**
 * JOBUI-02/D-16: renders progress.percent honestly -- an indeterminate
 * (animated, non-numeric) bar plus the step text when percent is null, or
 * a determinate bar/percentage when percent is a number. Never computes or
 * interpolates a percentage client-side. `status` is surfaced only in the
 * progressbar's accessible label (no branching on it) so a screen reader
 * announces which Job this progress belongs to.
 */
export function JobProgressPanel({ progress, status }: JobProgressPanelProps) {
  const { percent, step, current, total } = progress;
  const label = `Job progress (${status})`;

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <h2 className="text-sm font-semibold text-zinc-200">Progress</h2>
      <div className="mt-3 space-y-2 text-xs">
        {percent === null ? (
          <div
            role="progressbar"
            aria-label={label}
            className="h-2 w-full overflow-hidden rounded bg-zinc-800"
          >
            <div className="h-full w-full animate-pulse bg-sky-400/60" />
          </div>
        ) : (
          <div
            role="progressbar"
            aria-label={label}
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-2 w-full overflow-hidden rounded bg-zinc-800"
          >
            <div
              className="h-full bg-sky-400"
              style={{ width: `${percent}%` }}
            />
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 text-zinc-400">
          <span>{step ?? "—"}</span>
          {percent !== null ? <span>{`${percent}%`}</span> : null}
        </div>

        {current !== null && total !== null ? (
          <div className="text-zinc-500">{`${current} / ${total}`}</div>
        ) : null}
      </div>
    </section>
  );
}
