"use client";

import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import type { JobEventsPage } from "../types";

type JobEventsPanelProps = {
  jobId: string;
  jobIsTerminal: boolean;
};

/**
 * JOBUI-03: lifecycle events list over GET /api/v1/jobs/{id}/events.
 * Renders generically over event_type/from_status/to_status/outcome/
 * event_at plus requested_by/reason/terminal_cause when present -- zero
 * job_type conditionals (D-17). Polls every 3s only while the Job is
 * non-terminal, via useApiQuery's shared polling mechanism (Plan 08).
 * Every Job gets a SUBMITTED event at creation, so the empty-list render
 * is defensive only, not a normal reachable state.
 */
export function JobEventsPanel({ jobId, jobIsTerminal }: JobEventsPanelProps) {
  const endpoint = `/api/v1/jobs/${encodeURIComponent(jobId)}/events`;
  const { result } = useApiQuery<JobEventsPage>(endpoint, {
    pollIntervalMs: 3000,
    shouldPoll: () => !jobIsTerminal,
  });

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <h2 className="text-sm font-semibold text-zinc-200">Events</h2>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !result.ok ? (
          <ErrorState failure={result} title="Failed to load events" />
        ) : result.data.items.length === 0 ? (
          <p className="text-sm text-zinc-500">No events recorded yet.</p>
        ) : (
          <ul className="space-y-2 text-xs">
            {result.data.items.map((event) => (
              <li
                key={event.id}
                className="border-t border-zinc-800 pt-2 first:border-t-0 first:pt-0"
              >
                <div className="flex flex-wrap items-center gap-2 font-mono text-zinc-200">
                  <span>{event.event_type}</span>
                  <span className="text-zinc-500">
                    {event.from_status ?? "—"} → {event.to_status ?? "—"}
                  </span>
                  <span className="text-zinc-400">{event.outcome}</span>
                  <span className="text-zinc-500">{event.event_at}</span>
                </div>
                {event.requested_by || event.reason || event.terminal_cause ? (
                  <div className="mt-1 space-y-0.5 text-zinc-400">
                    {event.requested_by ? (
                      <div>requested by: {event.requested_by}</div>
                    ) : null}
                    {event.reason ? <div>reason: {event.reason}</div> : null}
                    {event.terminal_cause ? (
                      <div>cause: {event.terminal_cause}</div>
                    ) : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
