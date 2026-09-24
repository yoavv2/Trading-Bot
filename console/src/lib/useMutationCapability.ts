"use client";

import { useApiQuery } from "./useApiQuery";
import type { ApiResult } from "./api";
import type { JobTypesCatalog } from "../components/jobs/types";

const JOB_TYPES_ENDPOINT = "/api/v1/job-types";

export type MutationCapability = {
  state: "enabled" | "disabled" | "unknown";
  reason: string | null;
  catalog: JobTypesCatalog | null;
  loading: boolean;
};

/**
 * Pure mapping from a GET /api/v1/job-types ApiResult to the D-20/D-21
 * three-state mutation-capability signal. Exported separately from the
 * hook so the mapping itself is unit-testable without rendering a
 * component. `result === null` (still loading, nothing has resolved yet)
 * and a failed fetch both map to "unknown" — this hook never assumes
 * mutations are enabled absent a confirmed successful response, mirroring
 * KillSwitchBanner's honest-unknown pattern.
 */
export function mutationCapabilityFrom(
  result: ApiResult<JobTypesCatalog> | null,
): Omit<MutationCapability, "loading"> {
  if (result === null || !result.ok) {
    return {
      state: "unknown",
      reason: "Mutation availability unknown — GET /api/v1/job-types failed",
      catalog: null,
    };
  }
  if (result.data.mutations_enabled) {
    return { state: "enabled", reason: null, catalog: result.data };
  }
  return {
    state: "disabled",
    reason: "Mutations disabled on this deployment",
    catalog: result.data,
  };
}

/**
 * D-20/D-21: three-state ("enabled" | "disabled" | "unknown") mutation
 * capability signal, sourced from the single job-type catalog endpoint.
 * Consumers (submission forms, Cancel trigger, "New Job"/"Run backtest")
 * disable their controls with `reason` unless state is "enabled".
 */
export function useMutationCapability(): MutationCapability {
  const { loading, result } = useApiQuery<JobTypesCatalog>(JOB_TYPES_ENDPOINT);
  return { ...mutationCapabilityFrom(result), loading };
}
