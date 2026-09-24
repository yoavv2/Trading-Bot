"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi, type ApiFailure } from "@/lib/api";
import { ErrorState } from "@/components/ErrorState";
import type { JobLogLine, JobLogsPage } from "../types";

const PAGE_LIMIT = 100;
const POLL_INTERVAL_MS = 3000;
// Bounds the has_more drain loop so a pathological log volume can never spin
// a single tick forever (T-19-10-04).
const MAX_PAGES_PER_TICK = 20;

type JobLogsPanelProps = {
  jobId: string;
  jobIsTerminal: boolean;
};

/**
 * JOBUI-03: cursor-based log tail over GET /api/v1/jobs/{id}/logs. Fetches
 * page after page (bounded by MAX_PAGES_PER_TICK) while has_more is true,
 * tracks the highest sequence appended so an overlapping/re-fetched page
 * never duplicates a line, and polls every 3s only while the Job is
 * non-terminal -- a transition to terminal runs one final fetch, then
 * stops. Log message/context are untrusted handler output (T-19-10-01):
 * rendered as plain React text/JSON.stringify only -- no raw-HTML
 * injection API is used anywhere in this file.
 */
export function JobLogsPanel({ jobId, jobIsTerminal }: JobLogsPanelProps) {
  const [lines, setLines] = useState<JobLogLine[]>([]);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [fetchedOnce, setFetchedOnce] = useState(false);
  const [follow, setFollow] = useState(true);

  const cursorRef = useRef<number | null>(null);
  const lastSequenceRef = useRef(0);
  const hasEverSucceededRef = useRef(false);
  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const jobIsTerminalRef = useRef(jobIsTerminal);
  const prevTerminalRef = useRef(jobIsTerminal);
  const followRef = useRef(follow);
  const tickRef = useRef<() => Promise<void>>(async () => {});
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Keep latest-value refs current for async callbacks (timers, fetch
  // .then) -- assigned in no-dependency-array effects, never during
  // render, matching the useApiQuery precedent.
  useEffect(() => {
    jobIsTerminalRef.current = jobIsTerminal;
  });
  useEffect(() => {
    followRef.current = follow;
  });

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const scheduleNextTick = useCallback(() => {
    clearTimer();
    if (jobIsTerminalRef.current) {
      return;
    }
    if (typeof document !== "undefined" && document.hidden) {
      // Paused while hidden; the visibilitychange listener below resumes.
      return;
    }
    timerRef.current = setTimeout(() => {
      void tickRef.current();
    }, POLL_INTERVAL_MS);
  }, [clearTimer]);

  const runTick = useCallback(async () => {
    inFlightRef.current = true;
    let hasMore = true;
    let pagesFetched = 0;

    while (hasMore && pagesFetched < MAX_PAGES_PER_TICK) {
      const query =
        cursorRef.current !== null
          ? `?limit=${PAGE_LIMIT}&after_sequence=${cursorRef.current}`
          : `?limit=${PAGE_LIMIT}`;
      const result = await fetchApi<JobLogsPage>(
        `/api/v1/jobs/${encodeURIComponent(jobId)}/logs${query}`,
      );
      pagesFetched += 1;

      if (!mountedRef.current) {
        inFlightRef.current = false;
        return;
      }

      if (!result.ok) {
        // Only surface an error state if nothing has ever loaded yet -- a
        // poll failure after a successful load keeps showing the last
        // known-good lines and simply retries on the next tick.
        if (!hasEverSucceededRef.current) {
          setError(result);
        }
        hasMore = false;
        break;
      }

      setError(null);
      hasEverSucceededRef.current = true;
      const page = result.data;
      if (page.items.length > 0) {
        const appended = page.items.filter(
          (item) => item.sequence > lastSequenceRef.current,
        );
        if (appended.length > 0) {
          lastSequenceRef.current = appended[appended.length - 1].sequence;
          setLines((prev) => prev.concat(appended));
        }
      }
      cursorRef.current = page.next_after_sequence ?? cursorRef.current;
      hasMore = page.has_more;
    }

    inFlightRef.current = false;
    if (mountedRef.current) {
      setFetchedOnce(true);
      scheduleNextTick();
    }
  }, [jobId, scheduleNextTick]);

  useEffect(() => {
    tickRef.current = runTick;
  }, [runTick]);

  // Initial fetch on mount / jobId change.
  useEffect(() => {
    mountedRef.current = true;
    hasEverSucceededRef.current = false;
    cursorRef.current = null;
    lastSequenceRef.current = 0;
    // Deliberate: this hand-rolled tail (no SWR/TanStack per plan scope)
    // resets its local state and kicks off an immediate in-flight fetch on
    // mount/jobId change; runTick always resolves fetchedOnce via its own
    // state update.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLines([]);
    setFetchedOnce(false);
    void tickRef.current();
    return () => {
      mountedRef.current = false;
      clearTimer();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // A non-terminal -> terminal transition runs one final fetch, then the
  // scheduler above naturally stops (jobIsTerminalRef is already true by
  // the time scheduleNextTick runs, per effect declaration order).
  useEffect(() => {
    if (!prevTerminalRef.current && jobIsTerminal) {
      clearTimer();
      void tickRef.current();
    }
    prevTerminalRef.current = jobIsTerminal;
  }, [jobIsTerminal, clearTimer]);

  // Pause while hidden, resume with one immediate tick (if nothing is
  // already in flight) on regain -- same visibilitychange approach as
  // useApiQuery.
  useEffect(() => {
    function handleVisibilityChange() {
      if (document.hidden) {
        clearTimer();
        return;
      }
      if (!inFlightRef.current && !jobIsTerminalRef.current) {
        void tickRef.current();
      }
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [clearTimer]);

  // Auto-scroll to bottom after an append, only while "Follow" is checked.
  useEffect(() => {
    if (followRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-200">Logs</h2>
        <label className="flex items-center gap-1 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
          />
          Follow
        </label>
      </div>
      <div className="mt-3">
        {error ? (
          <ErrorState failure={error} title="Failed to load logs" />
        ) : !fetchedOnce ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : lines.length === 0 ? (
          <p className="text-sm text-zinc-500">
            {jobIsTerminal
              ? "No log lines were recorded for this Job."
              : "No log lines yet — this Job hasn't started emitting logs. This panel updates automatically once it does."}
          </p>
        ) : (
          <div
            ref={containerRef}
            className="max-h-96 space-y-1 overflow-y-auto font-mono text-xs text-zinc-300"
          >
            {lines.map((line) => (
              <div key={line.sequence} data-testid="log-line">
                <span className="text-zinc-500">{line.sequence}</span>{" "}
                <span className="text-zinc-500">{line.logged_at}</span>{" "}
                <span className="text-zinc-400">{line.level.toUpperCase()}</span>{" "}
                {line.event_code ? (
                  <span className="text-zinc-400">[{line.event_code}]</span>
                ) : null}{" "}
                <span>{line.message}</span>
                {line.context && Object.keys(line.context).length > 0 ? (
                  <span className="ml-1 text-zinc-500">
                    {JSON.stringify(line.context)}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
