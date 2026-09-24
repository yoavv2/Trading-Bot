"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi, type ApiResult } from "./api";

export type UseApiQueryOptions<T> = {
  // Positive interval in ms to poll on; omitted/non-positive disables polling.
  pollIntervalMs?: number;
  // Evaluated against the last successful response body. Polling stops the
  // instant this returns false; undefined means "poll until any predicate
  // stops it" (i.e. always true).
  shouldPoll?: (data: T) => boolean;
};

export type QueryState<T> = {
  loading: boolean;
  result: ApiResult<T> | null;
  refetch: () => void;
  // Whether a poll tick is currently scheduled or would be scheduled per
  // the active rule below, ignoring document.hidden — drives the
  // "Auto-refreshing every {N}s" / "Auto-refresh stopped" indicator.
  polling: boolean;
};

/**
 * Minimal fetch instrument shared by every console screen (CONS-02/CONS-03),
 * with optional silent polling (JOBUI-05). Fetches `endpoint` on mount and
 * whenever it changes, exposes loading state, the last ApiResult (with its
 * as-of timestamp), a manual refetch() for the FetchMeta "Refresh" control,
 * and — when `options.pollIntervalMs` is set — a background poll loop.
 * Deliberately has no caching or data-fetching library dependency.
 *
 * Polling semantics:
 * - Active iff pollIntervalMs is a positive number AND (no successful
 *   response has landed yet, OR shouldPoll is undefined, OR
 *   shouldPoll(lastSuccessfulData) is true).
 * - Background ticks never flip `loading` and never overlap — the next
 *   tick is scheduled only after the previous one resolves (setTimeout
 *   chaining, not setInterval).
 * - A failed background tick keeps the last successful `result` and keeps
 *   polling; a successful tick replaces `result`.
 * - Paused while document.hidden is true; a visibilitychange to visible
 *   runs one immediate tick (if still active) and resumes the chain.
 */
export function useApiQuery<T>(
  endpoint: string,
  options?: UseApiQueryOptions<T>,
): QueryState<T> {
  const [loading, setLoading] = useState(true);
  const [result, setResult] = useState<ApiResult<T> | null>(null);
  const [polling, setPolling] = useState(false);

  const mountedRef = useRef(true);
  const requestIdRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Callers pass a fresh inline options object on every render; keep the
  // latest one in a ref rather than putting it in effect/callback deps so
  // scheduling logic never churns identities or restarts the poll chain.
  // Assigned in an effect below (not during render) per the react-hooks
  // "no ref writes during render" rule — the assignment only needs to be
  // current by the time an async callback (fetch .then, timer) reads it,
  // never synchronously during this render pass.
  const optionsRef = useRef(options);

  const hasSuccessRef = useRef(false);
  const lastSuccessDataRef = useRef<T | undefined>(undefined);

  // Holds the latest background-tick implementation so the scheduler can
  // invoke it without a circular useCallback dependency.
  const tickRef = useRef<() => void>(() => {});

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const isPollingActive = useCallback((): boolean => {
    const opts = optionsRef.current;
    if (
      !opts ||
      typeof opts.pollIntervalMs !== "number" ||
      opts.pollIntervalMs <= 0
    ) {
      return false;
    }
    if (!hasSuccessRef.current || !opts.shouldPoll) {
      return true;
    }
    return opts.shouldPoll(lastSuccessDataRef.current as T);
  }, []);

  const scheduleNextTick = useCallback(() => {
    clearTimer();
    const active = isPollingActive();
    setPolling(active);
    if (!active) {
      return;
    }
    if (typeof document !== "undefined" && document.hidden) {
      // Paused while the tab is hidden; the visibilitychange listener
      // below resumes the chain (with one immediate tick) on regain.
      return;
    }
    const intervalMs = optionsRef.current!.pollIntervalMs as number;
    timerRef.current = setTimeout(() => {
      tickRef.current();
    }, intervalMs);
  }, [clearTimer, isPollingActive]);

  const runBackgroundTick = useCallback(() => {
    const requestId = ++requestIdRef.current;
    fetchApi<T>(endpoint).then((next) => {
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return;
      }
      if (next.ok) {
        hasSuccessRef.current = true;
        lastSuccessDataRef.current = next.data;
        setResult(next);
      }
      // A failed tick intentionally does not replace `result` and does
      // not reset hasSuccessRef/lastSuccessDataRef — polling continues
      // to be evaluated against the last successful response.
      scheduleNextTick();
    });
  }, [endpoint, scheduleNextTick]);

  const runFetch = useCallback(() => {
    const requestId = ++requestIdRef.current;
    clearTimer();
    setLoading(true);
    fetchApi<T>(endpoint).then((next) => {
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return;
      }
      setResult(next);
      setLoading(false);
      if (next.ok) {
        hasSuccessRef.current = true;
        lastSuccessDataRef.current = next.data;
      }
      scheduleNextTick();
    });
  }, [endpoint, clearTimer, scheduleNextTick]);

  // "Latest ref" sync effects — run after every render, before the mount
  // effect below (declaration order), so both refs are current by the
  // time runFetch's async .then() or any timer callback reads them.
  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    tickRef.current = runBackgroundTick;
  }, [runBackgroundTick]);

  useEffect(() => {
    mountedRef.current = true;
    hasSuccessRef.current = false;
    lastSuccessDataRef.current = undefined;
    // Deliberate: this hand-rolled fetch instrument (no SWR/TanStack per plan
    // scope) needs an immediate "in flight" signal on mount and on endpoint
    // change, so eslint-plugin-react-hooks' set-state-in-effect check — tuned
    // for external-store sync patterns — flags this call. `runFetch` always
    // resolves loading via its guarded `.then()`, so this is safe.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    runFetch();
    return () => {
      mountedRef.current = false;
      clearTimer();
    };
  }, [runFetch, clearTimer]);

  useEffect(() => {
    function handleVisibilityChange() {
      if (document.hidden) {
        clearTimer();
        return;
      }
      if (isPollingActive()) {
        tickRef.current();
      }
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [clearTimer, isPollingActive]);

  return { loading, result, refetch: runFetch, polling };
}
