"use client";

import { useEffect, useRef, useState } from "react";
import { preflightStudy } from "./api";
import type { StudyPreflight } from "./research/types";

export type PreflightRequest = { strategy_version_ids: string[]; assets: string[]; range: { start: string; end: string } | null; development_start: string | null };

export type PreflightState = {
  /** The response for the CURRENT key, or null while it is loading / after a failure. */
  data: StudyPreflight | null;
  /** The newest response of any key, so tags keep their last status while a refresh runs. */
  last: StudyPreflight | null;
  loading: boolean;
  failure: string | null;
  key: string;
};

const DEBOUNCE_MS = 300;

export function preflightKey(request: PreflightRequest): string {
  return JSON.stringify(request);
}

/**
 * Debounced, cached study preflight. Every distinct request (versions, assets, range,
 * development start) is fetched once per page and kept; repeats answer from the cache.
 * A failed lookup leaves `data` null and sets `failure`: callers must treat that as
 * unknown coverage, not as covered.
 */
export function useStudyPreflight(request: PreflightRequest, enabled = true): PreflightState {
  const key = preflightKey(request);
  const cache = useRef(new Map<string, StudyPreflight>());
  const inFlight = useRef(new Map<string, Promise<void>>());
  const [state, setState] = useState<PreflightState>({ data: null, last: null, loading: false, failure: null, key: "" });

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const cached = cache.current.get(key);
    if (cached) {
      setState((current) => (current.key === key && current.data === cached ? current : { data: cached, last: cached, loading: false, failure: null, key }));
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      setState((current) => ({ ...current, data: null, loading: true, failure: null, key }));
      let pending = inFlight.current.get(key);
      if (!pending) {
        pending = preflightStudy(JSON.parse(key) as PreflightRequest).then((result) => {
          inFlight.current.delete(key);
          if (result.ok) {
            cache.current.set(key, (result.data as { preflight: StudyPreflight }).preflight);
          } else if (!cancelled) {
            setState((current) => (current.key === key ? { ...current, loading: false, failure: result.message } : current));
          }
        });
        inFlight.current.set(key, pending);
      }
      pending.then(() => {
        const data = cache.current.get(key);
        if (!cancelled && data) {
          setState({ data, last: data, loading: false, failure: null, key });
        }
      });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, enabled]);

  return state;
}
