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
 * Closed map of typed mutation error codes (Job POST routes, plus the
 * Phase 20 retry/control routes) to the exact UI-SPEC copy for that code.
 * Each entry is a function of the response's `detail` object so codes that
 * carry extra fields (job_type, reason, status, job_id, strategy_id) can
 * interpolate them. Keys are the fourteen codes `api/routes/jobs.py`
 * (submit/cancel/retry) can raise via `_error(...)`.
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
    let data: T;
    try {
      data = (await response.json()) as T;
    } catch {
      data = null as T;
    }
    return {
      ok: true,
      data,
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
