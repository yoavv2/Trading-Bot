"use client";

import { useApiQuery } from "./useApiQuery";

export type HealthBody = { status: string; service: string; version: string; timestamp: string; mode?: string };

export type ResearchModeState = { state: "research" | "trading" | "unknown"; loading: boolean };

/**
 * Whether the API this console talks to serves the research surface. Sourced from the
 * additive `mode` field of GET /health; "unknown" until a successful response arrives,
 * and for an API that predates the field. The Research navigation entry and every
 * research page gate on it, so the trading console never advertises routes that would
 * 404 against a trading-mode API.
 */
export function useResearchMode(): ResearchModeState {
  const { loading, result } = useApiQuery<HealthBody>("/health");
  if (!result || !result.ok) {
    return { state: "unknown", loading };
  }
  if (result.data.mode === "research") {
    return { state: "research", loading };
  }
  if (result.data.mode === "trading") {
    return { state: "trading", loading };
  }
  return { state: "unknown", loading };
}
