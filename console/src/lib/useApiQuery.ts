"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi, type ApiResult } from "./api";

export type QueryState<T> = {
  loading: boolean;
  result: ApiResult<T> | null;
  refetch: () => void;
};

/**
 * Minimal fetch instrument shared by every console screen (CONS-02/CONS-03).
 * Fetches `endpoint` on mount and whenever it changes, exposes loading state,
 * the last ApiResult (with its as-of timestamp), and a manual refetch() for the
 * FetchMeta "Refresh" control. Deliberately has no polling, caching, or
 * data-fetching library dependency.
 */
export function useApiQuery<T>(endpoint: string): QueryState<T> {
  const [loading, setLoading] = useState(true);
  const [result, setResult] = useState<ApiResult<T> | null>(null);
  const mountedRef = useRef(true);
  const requestIdRef = useRef(0);

  const runFetch = useCallback(() => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    fetchApi<T>(endpoint).then((next) => {
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        return;
      }
      setResult(next);
      setLoading(false);
    });
  }, [endpoint]);

  useEffect(() => {
    mountedRef.current = true;
    runFetch();
    return () => {
      mountedRef.current = false;
    };
  }, [runFetch]);

  return { loading, result, refetch: runFetch };
}
