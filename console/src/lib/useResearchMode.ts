"use client";

import type { ApiFailure } from "./api";
import { useApiQuery } from "./useApiQuery";

export type HealthBody = { status: string; service: string; version: string; timestamp: string; mode?: string };

export type ResearchModeState = {
  state: "research" | "trading" | "unknown";
  loading: boolean;
  /** The failed GET /health when the API could not be reached or answered an error. */
  failure: ApiFailure | null;
  refetch: () => void;
};

/**
 * Whether the API this console talks to serves the research surface. Sourced from the
 * additive `mode` field of GET /health; "unknown" until a successful response arrives,
 * and for an API that predates the field. Every research page gates on it, so the
 * console never renders research panels against a trading-mode API (their routes would
 * 404) and reports an unreachable or misconfigured API in one place.
 */
export function useResearchMode(): ResearchModeState {
  const { loading, result, refetch } = useApiQuery<HealthBody>("/health");
  if (!result) {
    return { state: "unknown", loading, failure: null, refetch };
  }
  if (!result.ok) {
    return { state: "unknown", loading, failure: result, refetch };
  }
  if (result.data.mode === "research") {
    return { state: "research", loading, failure: null, refetch };
  }
  if (result.data.mode === "trading") {
    return { state: "trading", loading, failure: null, refetch };
  }
  return { state: "unknown", loading, failure: null, refetch };
}
