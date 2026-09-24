// Closed Job status -> color/label map (JOBUI-01/02), per UI-SPEC "Closed Job
// status -> color/label map". A Job status is structurally a closed 5-value
// DB enum, but every mapping here still defaults defensively for any value
// this console version does not recognize yet.

export const TERMINAL_JOB_STATUSES = ["succeeded", "failed", "cancelled"] as const;

export function isTerminalJobStatus(status: string): boolean {
  return (TERMINAL_JOB_STATUSES as readonly string[]).includes(status);
}

const STATUS_COLOR: Readonly<Record<string, string>> = {
  queued: "text-zinc-400",
  running: "text-amber-400",
  succeeded: "text-emerald-400",
  failed: "text-red-400",
  cancelled: "text-zinc-500",
};

/**
 * Maps a Job status to its badge text color class. An unrecognized value
 * (should be structurally impossible per the DB enum) renders
 * `text-zinc-300` rather than a blank badge.
 */
export function jobStatusColor(status: string): string {
  return STATUS_COLOR[status] ?? "text-zinc-300";
}

// Unpadded badge markup — matches the existing statusColor() badges in
// RunsTable.tsx/RunHeaderPanel.tsx exactly, except at 600 weight (not 700):
// no px/py, no rounded background, no tracking-wide.
export const JOB_STATUS_BADGE_CLASS = "text-xs font-semibold uppercase";
