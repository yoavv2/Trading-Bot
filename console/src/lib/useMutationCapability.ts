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
 * component. Both `result === null` (still loading, nothing has resolved
 * yet) and a failed fetch map to "unknown" — this hook never assumes
 * mutations are enabled absent a confirmed successful response, mirroring
 * KillSwitchBanner's honest-unknown pattern. The two cases carry different
 * reasons: `result === null` has not actually failed anything yet, so its
 * `reason` stays null (nothing to report) rather than stating a failure
 * that has not happened; only a confirmed failed fetch gets the
 * "...GET /api/v1/job-types failed" reason text.
 */
export function mutationCapabilityFrom(
  result: ApiResult<JobTypesCatalog> | null,
): Omit<MutationCapability, "loading"> {
  if (result === null) {
    return { state: "unknown", reason: null, catalog: null };
  }
  if (!result.ok) {
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
