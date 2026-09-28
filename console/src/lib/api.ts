import type { JobReference } from "../components/jobs/types";

export type ApiSuccess<T> = { ok: true; data: T; endpoint: string; asOf: Date };
export type ApiFailure = {
  ok: false;
  endpoint: string; // always populated — CONS-02 requires naming the failing endpoint
  status: number | null; // null = network/proxy unreachable
  message: string;
  body?: unknown; // parsed JSON error body when available
  asOf: Date;
};
export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

export type MutationResult<T> =
  | { ok: true; data: T; replayed: boolean; status: number }
  | {
      ok: false;
      status: number | null;
      code: string | null;
      message: string;
      detail: Record<string, unknown> | null;
    };

/**
 * Fetches `endpoint` (an un-prefixed FastAPI path, e.g. "/api/v1/system") through the
 * Next.js `/backend/*` rewrite proxy and classifies the outcome into an explicit
 * success-or-error result. Never throws — every code path (success, HTTP error,
 * non-JSON body, network/proxy failure) resolves to an ApiResult naming the endpoint.
 */
export async function fetchApi<T>(endpoint: string): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(`/backend${endpoint}`, { cache: "no-store" });
  } catch {
    return {
      ok: false,
      endpoint,
      status: null,
      message: `${endpoint} is unreachable (network or proxy failure)`,
      asOf: new Date(),
    };
  }

  const asOf = new Date();
  const contentType = response.headers.get("content-type") ?? "";
  let parsedBody: unknown;
  let bodyParseFailed = false;

  if (contentType.includes("application/json")) {
    try {
      parsedBody = await response.json();
    } catch {
      bodyParseFailed = true;
    }
  } else {
    bodyParseFailed = true;
  }

  if (response.ok) {
    return {
      ok: true,
      data: parsedBody as T,
      endpoint,
      asOf,
    };
  }

  const detailMessage =
    !bodyParseFailed &&
    parsedBody &&
    typeof parsedBody === "object" &&
    "detail" in parsedBody &&
    typeof (parsedBody as { detail?: unknown }).detail === "string"
      ? (parsedBody as { detail: string }).detail
      : null;

  return {
    ok: false,
    endpoint,
    status: response.status,
    message: detailMessage
      ? `HTTP ${response.status}: ${detailMessage}`
      : `HTTP ${response.status}`,
    body: bodyParseFailed ? undefined : parsedBody,
    asOf,
  };
}

/**
 * Fallback copy for a control-route rejection whose `detail` is not a
 * `{code: string, ...}` object the console recognizes (e.g. an array-shaped
 * pydantic validation error, or an unmapped string). Declared once here and
 * referenced by both the `invalid_control_request` map entry and
 * `controlErrorMessage`'s own fallback, so the literal string appears
 * exactly once in this file.
 */
const CONTROL_REQUEST_REJECTED_COPY =
  "Request rejected — check the input and try again.";

/**
 * Closed map of typed mutation error codes (Job POST routes, plus the
 * Phase 20 retry/control routes) to the exact UI-SPEC copy for that code.
 * Each entry is a function of the response's `detail` object so codes that
 * carry extra fields (job_type, reason, status, job_id, strategy_id) can
 * interpolate them. Keys are the eighteen codes `api/routes/jobs.py`
 * (submit/cancel/retry) and the `api/routes/controls.py` mutation routes
 * can raise via their `_error(...)`-style helpers.
 */
export const MUTATION_ERROR_COPY: Readonly<
  Record<string, (detail: Record<string, unknown> | null) => string>
> = {
  missing_idempotency_key: () =>
    "Missing idempotency key — this is a console bug, not an operator error. Reload the page and try again.",
  invalid_idempotency_key: () =>
    "Invalid idempotency key format — this is a console bug, not an operator error. Reload the page and try again.",
  unknown_job_type: (detail) => {
    const jobType =
      detail && typeof detail.job_type === "string" ? detail.job_type : "unknown";
    return `"${jobType}" is not a registered Job type.`;
  },
  invalid_job_payload: (detail) => {
    const reason =
      detail && typeof detail.reason === "string" && detail.reason.length > 0
        ? detail.reason
        : "invalid payload";
    return `This submission was rejected: ${reason}.`;
  },
  idempotency_key_conflict: () =>
    "Idempotency key reused with a different payload — this is a console bug, not an operator error. Reload the page and try again.",
  invalid_cancellation_reason: () =>
    "Cancellation reason must be 500 characters or fewer.",
  job_not_found: (detail) => {
    const jobId = detail && typeof detail.job_id === "string" ? detail.job_id : "unknown";
    return `Job ${jobId} was not found.`;
  },
  job_not_cancellable: (detail) => {
    const cancelStatus =
      detail && typeof detail.status === "string" ? detail.status : "unknown";
    return `This Job is already ${cancelStatus} and cannot be cancelled.`;
  },
  mutations_disabled: () => "Mutations disabled on this deployment",
  job_not_cancellable_running: () => "Not cancellable once running",
  reconciliation_required: () =>
    "Retry blocked — the original Job's outcome is uncertain. Run reconciliation first, then retry.",
  retry_exists: () => "This Job already has a retry.",
  job_not_retryable: () =>
    "This Job cannot be retried — retry is only available for FAILED or CANCELLED Jobs.",
  invalid_retry_payload: (detail) => {
    const reason =
      detail && typeof detail.reason === "string" && detail.reason.trim().length > 0
        ? detail.reason
        : "the original payload is no longer valid";
    return `This Job can no longer be retried: ${reason}.`;
  },
  strategy_not_found: (detail) => {
    const strategyId =
      detail && typeof detail.strategy_id === "string" ? detail.strategy_id : "unknown";
    return `Strategy ${strategyId} was not found.`;
  },
  invalid_control_target: () =>
    "Invalid target state — this is a console bug, not an operator error. Reload the page and try again.",
  invalid_control_reason: () =>
    "Reason is required and must be 500 characters or fewer.",
  invalid_control_request: () => CONTROL_REQUEST_REJECTED_COPY,
};

/**
 * Maps a mutation error `code` (from `{"detail": {"code": ..., ...}}`) to its
 * UI-SPEC copy, or a generic fallback for an unmapped/null code. Never
 * throws — `code` may be any string the server sent, including one this
 * console version does not recognize yet.
 */
export function mutationErrorMessage(
  code: string | null,
  detail: Record<string, unknown> | null,
): string {
  if (code !== null && Object.hasOwn(MUTATION_ERROR_COPY, code)) {
    return MUTATION_ERROR_COPY[code](detail);
  }
  return `Operation failed (${code ?? "unknown"}). Try again; if it persists, check the API logs.`;
}

/**
 * True when a failed mutation may nonetheless have been applied server-side:
 * a transport failure (`status === null`), a 5xx, or an unreadable 2xx (the
 * response, not the write, was lost). Callers use it to re-verify displayed
 * state instead of trusting the pre-mutation snapshot (WR-C-02/WR-C-03).
 */
export function isOutcomeUncertain(
  result: Extract<MutationResult<unknown>, { ok: false }>,
): boolean {
  return result.status === null || result.status >= 500 || result.status < 300;
}

/**
 * Failure result for a 2xx whose body is unparseable or not the expected
 * shape (WR-C-03). The mutation most likely committed, so this is reported as
 * a failure with the (2xx) status preserved -- `isOutcomeUncertain` is true --
 * rather than as `ok: true` with data the caller would dereference.
 */
function unreadableSuccess(
  endpoint: string,
  status: number,
): Extract<MutationResult<never>, { ok: false }> {
  return {
    ok: false,
    status,
    code: null,
    message: `${endpoint} returned an unreadable response. Reload and verify the current state.`,
    detail: null,
  };
}

function isJobReferenceBody(data: unknown): boolean {
  return (
    typeof data === "object" &&
    data !== null &&
    typeof (data as { job_id?: unknown }).job_id === "string"
  );
}

function hasBooleanChanged(data: unknown): boolean {
  return (
    typeof data === "object" &&
    data !== null &&
    typeof (data as { changed?: unknown }).changed === "boolean"
  );
}

/**
 * Shared POST implementation for the Job mutation routes. Never throws.
 * `detailDefaults` seeds the error `detail` object with caller-known fields
 * (e.g. the Job id being cancelled) that the server does not always echo
 * back verbatim; a server-provided field of the same name wins.
 */
async function postJson<T>(
  endpoint: string,
  body: unknown,
  idempotencyKey: string,
  detailDefaults?: Record<string, unknown>,
): Promise<MutationResult<T>> {
  let response: Response;
  try {
    response = await fetch(`/backend${endpoint}`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(body),
    });
  } catch {
    return {
      ok: false,
      status: null,
      code: null,
      message: `${endpoint} is unreachable (network or proxy failure)`,
      detail: null,
    };
  }

  const status = response.status;

  if (response.ok) {
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      return unreadableSuccess(endpoint, status);
    }
    if (!isJobReferenceBody(data)) {
      return unreadableSuccess(endpoint, status);
    }
    return {
      ok: true,
      data: data as T,
      replayed: response.headers.get("Idempotency-Replayed") === "true",
      status,
    };
  }

  let parsedBody: unknown = null;
  try {
    parsedBody = await response.json();
  } catch {
    parsedBody = null;
  }

  let detail: Record<string, unknown> | null = null;
  if (parsedBody && typeof parsedBody === "object" && "detail" in parsedBody) {
    const rawDetail = (parsedBody as { detail?: unknown }).detail;
    if (rawDetail && typeof rawDetail === "object") {
      detail = { ...(detailDefaults ?? {}), ...(rawDetail as Record<string, unknown>) };
    }
  }
  if (detail === null && detailDefaults) {
    detail = { ...detailDefaults };
  }

  const code = detail && typeof detail.code === "string" ? detail.code : null;

  return {
    ok: false,
    status,
    code,
    message: mutationErrorMessage(code, detail),
    detail,
  };
}

/**
 * Submits a new Job (SC6: the sole mutating fetch call for job submission).
 * `idempotencyKey` is generated and owned by the caller (form/dialog).
 */
export function submitJob(
  request: { job_type: string; payload: Record<string, unknown> },
  idempotencyKey: string,
): Promise<MutationResult<JobReference>> {
  return postJson<JobReference>("/api/v1/jobs", request, idempotencyKey);
}

/**
 * Requests cancellation of `jobId`. `reason` is passed through exactly as
 * given (D-15: the caller already trimmed it and nulled a blank string).
 */
export function cancelJob(
  jobId: string,
  reason: string | null,
  idempotencyKey: string,
): Promise<MutationResult<JobReference>> {
  return postJson<JobReference>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`,
    { reason },
    idempotencyKey,
    { job_id: jobId },
  );
}

/**
 * Requests a retry of `jobId` (D-16/D-20). `idempotencyKey` is generated and
 * owned by the caller (the Retry confirmation dialog), one per dialog
 * opening, reused across a transport-failure retry of the same attempt.
 */
export function retryJob(
  jobId: string,
  idempotencyKey: string,
): Promise<MutationResult<JobReference>> {
  return postJson<JobReference>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}/retry`,
    {},
    idempotencyKey,
    { job_id: jobId },
  );
}

/** Read-model response of GET /api/v1/controls/strategies/{id} (D-31, read-only). */
export type StrategyControlState = {
  strategy_id: string;
  status: "enabled" | "disabled";
  updated_at: string | null;
};

/** Response body of PUT /api/v1/controls/kill-switch. */
export type KillSwitchControlResponse = {
  state: "tripped" | "armed";
  changed: boolean;
  run_id: string;
};

/** Response body of PUT /api/v1/controls/strategies/{id}. */
export type StrategyControlResponse = {
  strategy_id: string;
  status: "enabled" | "disabled";
  changed: boolean;
  run_id: string;
};

/**
 * Maps a control-route rejection's raw `detail` value to UI-SPEC copy
 * (D-10/D-11/D-14, Amendment 2026-09-28 #3-4). An object carrying a string
 * `code` uses the shared MUTATION_ERROR_COPY table; a plain string is
 * rendered verbatim (the existing 404 pattern for some control errors);
 * any other shape (e.g. an array-shaped pydantic validation error) falls
 * back to the defensive copy. Never throws.
 */
export function controlErrorMessage(detail: unknown): string {
  if (
    detail &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    typeof (detail as { code?: unknown }).code === "string"
  ) {
    const code = (detail as { code: string }).code;
    return mutationErrorMessage(code, detail as Record<string, unknown>);
  }
  if (typeof detail === "string") {
    return detail;
  }
  return CONTROL_REQUEST_REJECTED_COPY;
}

/**
 * Shared PUT implementation for the control mutation routes (D-10):
 * idempotent by explicit target state, so unlike `postJson` it sends no
 * `Idempotency-Key` and always reports `replayed: false` on success. Never
 * throws.
 */
async function putJson<T>(
  endpoint: string,
  body: unknown,
): Promise<MutationResult<T>> {
  let response: Response;
  try {
    response = await fetch(`/backend${endpoint}`, {
      method: "PUT",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch {
    return {
      ok: false,
      status: null,
      code: null,
      message: `${endpoint} is unreachable (network or proxy failure)`,
      detail: null,
    };
  }

  const status = response.status;

  if (response.ok) {
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      return unreadableSuccess(endpoint, status);
    }
    if (!hasBooleanChanged(data)) {
      return unreadableSuccess(endpoint, status);
    }
    return {
      ok: true,
      data: data as T,
      replayed: false,
      status,
    };
  }

  let parsedBody: unknown = null;
  try {
    parsedBody = await response.json();
  } catch {
    parsedBody = null;
  }

  const rawDetail =
    parsedBody && typeof parsedBody === "object" && "detail" in parsedBody
      ? (parsedBody as { detail?: unknown }).detail
      : null;

  const detail: Record<string, unknown> | null =
    rawDetail && typeof rawDetail === "object" && !Array.isArray(rawDetail)
      ? (rawDetail as Record<string, unknown>)
      : null;

  const code = detail && typeof detail.code === "string" ? detail.code : null;

  return {
    ok: false,
    status,
    code,
    message: controlErrorMessage(rawDetail ?? null),
    detail,
  };
}

/** Trips the kill switch (D-10). Sends no Idempotency-Key. */
export function tripKillSwitch(
  reason: string,
): Promise<MutationResult<KillSwitchControlResponse>> {
  return putJson<KillSwitchControlResponse>("/api/v1/controls/kill-switch", {
    state: "tripped",
    reason,
  });
}

/** Resets (arms) the kill switch (D-10/D-14). Sends no Idempotency-Key. */
export function resetKillSwitch(
  reason: string,
): Promise<MutationResult<KillSwitchControlResponse>> {
  return putJson<KillSwitchControlResponse>("/api/v1/controls/kill-switch", {
    state: "armed",
    reason,
  });
}

/** Enables `strategyId` (D-10). Sends no Idempotency-Key. */
export function enableStrategy(
  strategyId: string,
  reason: string,
): Promise<MutationResult<StrategyControlResponse>> {
  return putJson<StrategyControlResponse>(
    `/api/v1/controls/strategies/${encodeURIComponent(strategyId)}`,
    { status: "enabled", reason },
  );
}

/** Disables `strategyId` (D-10). Sends no Idempotency-Key. */
export function disableStrategy(
  strategyId: string,
  reason: string,
): Promise<MutationResult<StrategyControlResponse>> {
  return putJson<StrategyControlResponse>(
    `/api/v1/controls/strategies/${encodeURIComponent(strategyId)}`,
    { status: "disabled", reason },
  );
}
