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
 * Operator copy for `invalid_job_payload` / `invalid_retry_payload` `reason`
 * values that are not self-explanatory. These are `reason` enum values on a
 * 422 (not top-level error codes). Any reason without an entry is shown
 * verbatim, as before.
 */
const PAYLOAD_REASON_COPY: Readonly<Record<string, string>> = {
  as_of_session_out_of_calendar_range:
    "the as-of session date is outside the exchange calendar's supported range",
  date_range_out_of_calendar_range:
    "the date range is outside the exchange calendar's supported range",
};

function payloadReasonCopy(reason: string): string {
  return Object.hasOwn(PAYLOAD_REASON_COPY, reason)
    ? PAYLOAD_REASON_COPY[reason]
    : reason;
}

/**
 * Closed map of typed mutation error codes (Job POST routes, plus the
 * Phase 20 retry/control routes) to the exact UI-SPEC copy for that code.
 * Each entry is a function of the response's `detail` object so codes that
 * carry extra fields (job_type, reason, status, job_id, strategy_id) can
 * interpolate them. Keys are the codes `api/routes/jobs.py`
 * (submit/cancel/retry), the `api/routes/controls.py` mutation routes and
 * the app-level unhandled-exception handler (`internal_error`) can emit.
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
        ? payloadReasonCopy(detail.reason)
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
        ? payloadReasonCopy(detail.reason)
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
  // Codes added by the backend review fixes (WR-C-09). Not in UI-SPEC yet.
  strategy_archived: (detail) => {
    const strategyId =
      detail && typeof detail.strategy_id === "string" ? detail.strategy_id : "unknown";
    return `Strategy ${strategyId} is archived and cannot be enabled or disabled.`;
  },
  control_state_unavailable: () =>
    "Control state is unavailable — the API could not read the stored control state. Nothing was changed; check that database migrations are current, then try again.",
  control_write_failed: () =>
    "The control change could not be saved — nothing was committed. Try again; if it persists, check the API and database logs.",
  internal_error: () =>
    "The API hit an unexpected error. Reload to verify the current state, then try again; if it persists, check the API logs.",
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
 * Copy for a control-route failure that carries no recognized `{code}` and
 * arrived as a 5xx / proxy error (WR-C-04). UI-SPEC's literal "Request
 * rejected" row was written for 4xx validation shapes; telling an operator
 * their input is wrong while the API is down (e.g. a Trip attempt during an
 * outage) is misleading, and the request may not have been processed.
 */
function controlOutageMessage(status: number): string {
  return `Operation failed (HTTP ${status}). The change may not have been applied — reload to verify the current state, then try again; if it persists, check the API logs.`;
}

/**
 * Maps a control-route rejection's raw `detail` value to UI-SPEC copy
 * (D-10/D-11/D-14, Amendment 2026-09-28 #3-4). An object carrying a string
 * `code` uses the shared MUTATION_ERROR_COPY table (checked first, so a typed
 * 5xx such as a 503 keeps its own copy); a plain string is rendered verbatim
 * (the existing 404 pattern for some control errors); any other shape falls
 * back to the outage copy when the HTTP `status` is 5xx and to the defensive
 * "Request rejected" copy otherwise (e.g. an array-shaped pydantic 4xx).
 * Never throws.
 */
export function controlErrorMessage(
  detail: unknown,
  status?: number | null,
): string {
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
  if (typeof status === "number" && status >= 500) {
    return controlOutageMessage(status);
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
    message: controlErrorMessage(rawDetail ?? null, status),
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

// ---------------------------------------------------------------------------
// Research section (S4). Synchronous research writes go through ONE helper that
// names the HTTP method first, so the console-side mutation inventory test and
// tests/test_console_api_contract.py can enumerate them. No Idempotency-Key: the
// research routes are explicit, re-runnable writes on the research database.
// ---------------------------------------------------------------------------

/** Operator copy for the research routes' closed error codes. */
const RESEARCH_ERROR_COPY: Readonly<
  Record<string, (detail: Record<string, unknown> | null) => string>
> = {
  draft_not_found: () => "This draft no longer exists (it may have been approved or deleted).",
  version_not_found: () => "This strategy version was not found.",
  draft_invalid: (detail) => {
    const errors = detail && Array.isArray(detail.errors) ? detail.errors.length : 0;
    return `The draft is invalid and cannot be approved: ${errors} finding(s). Fix them in the editor first.`;
  },
  invalid_draft_input: (detail) =>
    `Invalid input: ${detail && typeof detail.field === "string" ? detail.field : "field"} ${
      detail && typeof detail.reason === "string" ? detail.reason : "is not acceptable"
    }.`,
  invalid_request: (detail) =>
    `Request rejected${detail && typeof detail.reason === "string" ? ` (${detail.reason})` : ""} — this is a console bug, not an operator error.`,
  study_not_found: () => "This study was not found.",
  revision_not_found: () => "This study revision was not found.",
  invalid_study_settings: (detail) =>
    `Study settings rejected: ${detail && typeof detail.field === "string" ? detail.field : "field"} ${
      detail && typeof detail.reason === "string" ? detail.reason : "is not acceptable"
    }.`,
  revision_not_ready: (detail) => {
    const errors = detail && Array.isArray(detail.errors) ? detail.errors.length : 0;
    return `The revision is not ready: ${errors} readiness error(s). Nothing was started.`;
  },
  run_already_started: () => "This revision already has its initial run; create a new revision to run again.",
  freeze_not_allowed: (detail) =>
    `Freeze refused: ${detail && typeof detail.reason === "string" ? detail.reason.replace(/_/g, " ") : "not allowed"}.`,
  already_frozen: () => "This revision is already frozen.",
  final_test_not_frozen: () => "Freeze the candidate and its acceptance values before running the final test.",
  final_test_already_run: () => "The final test of this revision already ran; use a technical rerun.",
  final_test_not_run: () => "There is no completed final test to rerun.",
  inputs_not_frozen: () => "The inputs of this revision are not frozen.",
  inputs_changed_after_freeze: () => "The inputs changed after the freeze; this revision's Jobs refuse to run.",
  asset_list_not_found: () => "This saved list was not found.",
  asset_list_name_taken: () => "A saved list with this name already exists.",
  invalid_asset_list: (detail) =>
    `Saved list rejected: ${detail && typeof detail.field === "string" ? detail.field : "field"} ${
      detail && typeof detail.reason === "string" ? detail.reason : "is not acceptable"
    }.`,
  asset_not_in_catalog: () => "This ticker is not in the catalog.",
  // S5 assistant (closed codes of services/research/assistant.py)
  ai_disabled: () => "The assistant is disabled. Enable it with TRADING_PLATFORM_RESEARCH__AI__ENABLED=true, set ANTHROPIC_API_KEY and both limits, then restart the API.",
  ai_not_configured: (detail) =>
    `The assistant is enabled but not configured: ${Array.isArray(detail?.missing) ? (detail!.missing as string[]).join("; ") : "missing settings"}. Restart the API after setting them.`,
  ai_input_too_long: (detail) =>
    `The request is too long (${detail && typeof detail.length === "number" ? detail.length : "?"} characters; limit ${detail && typeof detail.limit === "number" ? detail.limit : "?"}). Shorten the description or the YAML.`,
  ai_daily_limit_reached: (detail) =>
    `The daily assistant allowance is used up (${detail && typeof detail.used === "number" ? detail.used : "?"} of ${detail && typeof detail.limit === "number" ? detail.limit : "?"} requests, retries included). It resets 24 hours after the oldest request; raise TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY to change it.`,
  ai_revision_limit_reached: (detail) =>
    `This draft reached its assistant revision limit (${detail && typeof detail.limit === "number" ? detail.limit : "?"}). Edit the YAML by hand or duplicate the draft.`,
  ai_busy: () => "Another assistant request is still in flight. Wait for it to finish, then try again.",
  ai_timeout: (detail) =>
    `The assistant did not answer within the deadline${detail && typeof detail.deadline_seconds === "number" ? ` (${detail.deadline_seconds}s)` : ""}. The attempt was charged and recorded; try again or raise TRADING_PLATFORM_RESEARCH__AI__TIMEOUT_SECONDS.`,
  ai_refused: () => "The provider declined to answer this request. Rephrase it; the attempt was recorded.",
  ai_output_truncated: (detail) =>
    `The assistant's output hit the output-token limit (${detail && typeof detail.limit === "number" ? detail.limit : "?"}). Ask for a smaller strategy or raise TRADING_PLATFORM_RESEARCH__AI__MAX_OUTPUT_TOKENS.`,
  ai_provider_error: (detail) =>
    `The provider answered an error${detail && typeof detail.provider_status === "number" ? ` (HTTP ${detail.provider_status})` : ""}${
      detail && typeof detail.provider_message === "string" && detail.provider_message ? `: ${detail.provider_message}` : ""
    }. Nothing was applied; the attempt was recorded.`,
  ai_provider_unavailable: () => "The provider could not be reached (network). Nothing was applied; the attempt was recorded.",
  ai_auth_failed: (detail) =>
    `The provider rejected the API key${detail && typeof detail.provider_message === "string" && detail.provider_message ? ` (${detail.provider_message})` : ""}. Check ANTHROPIC_API_KEY and restart the API.`,
  ai_rate_limited: () => "The provider rate-limited this request. Wait a moment and try again; the attempt was charged.",
  ai_proposal_not_applicable: () => "This proposal ended without a specification and cannot be applied.",
  ai_draft_not_found: () => "This assistant proposal was not found.",
  invalid_assistant_input: (detail) =>
    `Assistant input rejected: ${detail && typeof detail.field === "string" ? detail.field : "field"} ${
      detail && typeof detail.reason === "string" ? detail.reason : "is not acceptable"
    }.`,
};

export function researchErrorMessage(
  code: string | null,
  detail: Record<string, unknown> | null,
): string {
  if (code !== null && Object.hasOwn(RESEARCH_ERROR_COPY, code)) {
    return RESEARCH_ERROR_COPY[code](detail);
  }
  return mutationErrorMessage(code, detail);
}

async function researchMutation<T>(
  method: "POST" | "PUT" | "DELETE",
  endpoint: string,
  body?: unknown,
): Promise<MutationResult<T>> {
  let response: Response;
  try {
    response = await fetch(`/backend${endpoint}`, {
      method,
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
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
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }
  if (response.ok) {
    if (!parsed || typeof parsed !== "object") {
      return unreadableSuccess(endpoint, status);
    }
    return { ok: true, data: parsed as T, replayed: false, status };
  }
  const rawDetail =
    parsed && typeof parsed === "object" && "detail" in parsed
      ? (parsed as { detail?: unknown }).detail
      : null;
  const detail =
    rawDetail && typeof rawDetail === "object" && !Array.isArray(rawDetail)
      ? (rawDetail as Record<string, unknown>)
      : null;
  const code = detail && typeof detail.code === "string" ? detail.code : null;
  return {
    ok: false,
    status,
    code,
    message:
      typeof rawDetail === "string" ? rawDetail : researchErrorMessage(code, detail),
    detail,
  };
}

export function createDraft(body: { title: string; yaml_text: string }) {
  return researchMutation<{ draft: unknown }>("POST", "/api/v1/research/strategies/drafts", body);
}
export function updateDraft(draftId: string, body: { title?: string; yaml_text?: string }) {
  return researchMutation<{ draft: unknown }>("PUT", `/api/v1/research/strategies/drafts/${encodeURIComponent(draftId)}`, body);
}
export function deleteDraft(draftId: string) {
  return researchMutation<{ deleted: boolean }>("DELETE", `/api/v1/research/strategies/drafts/${encodeURIComponent(draftId)}`);
}
export function duplicateDraft(draftId: string) {
  return researchMutation<{ draft: unknown }>("POST", `/api/v1/research/strategies/drafts/${encodeURIComponent(draftId)}/duplicate`, {});
}
export function approveDraft(draftId: string) {
  return researchMutation<{ version: unknown }>("POST", `/api/v1/research/strategies/drafts/${encodeURIComponent(draftId)}/approve`, {});
}
export function validateYamlText(yamlText: string) {
  return researchMutation<{ validation: unknown }>("POST", "/api/v1/research/strategies/validate", { yaml_text: yamlText });
}
export function editVersion(versionId: string) {
  return researchMutation<{ draft: unknown }>("POST", `/api/v1/research/strategies/versions/${encodeURIComponent(versionId)}/edit`, {});
}
export function duplicateVersion(versionId: string) {
  return researchMutation<{ draft: unknown }>("POST", `/api/v1/research/strategies/versions/${encodeURIComponent(versionId)}/duplicate`, {});
}
export function requestAssistantProposal(body: {
  user_text: string;
  draft_id?: string;
  base_yaml_text?: string;
  parent_ai_draft_id?: string;
  request_token?: string;
}) {
  return researchMutation<{ proposal: unknown }>("POST", "/api/v1/research/assistant/proposals", body);
}
export function applyAssistantProposal(aiDraftId: string, body: { draft_id?: string; title?: string }) {
  return researchMutation<{ draft: unknown; proposal: unknown; already_applied: boolean }>(
    "POST",
    `/api/v1/research/assistant/proposals/${encodeURIComponent(aiDraftId)}/apply`,
    body,
  );
}
export function createAssetList(body: { name: string; tickers: string[] }) {
  return researchMutation<{ list: unknown }>("POST", "/api/v1/research/asset-lists", body);
}
export function updateAssetList(listId: string, body: { name?: string; tickers?: string[] }) {
  return researchMutation<{ list: unknown }>("PUT", `/api/v1/research/asset-lists/${encodeURIComponent(listId)}`, body);
}
export function deleteAssetList(listId: string) {
  return researchMutation<{ deleted: boolean }>("DELETE", `/api/v1/research/asset-lists/${encodeURIComponent(listId)}`);
}
export function createStudy(body: { name: string; kind: string; settings: Record<string, unknown> }) {
  return researchMutation<{ study: unknown; revision: unknown }>("POST", "/api/v1/research/studies", body);
}
export function createRevision(studyId: string, settings: Record<string, unknown>) {
  return researchMutation<{ revision: unknown }>("POST", `/api/v1/research/studies/${encodeURIComponent(studyId)}/revisions`, { settings });
}
export function runRevision(revisionId: string) {
  return researchMutation<{ jobs: unknown }>("POST", `/api/v1/research/revisions/${encodeURIComponent(revisionId)}/run`, {});
}
export function freezeRevision(
  revisionId: string,
  body: { strategy_version_id: string; asset: string; acceptance: { constraint_value: number; objective_minimum: number }; co_leading_choice_reason?: string },
) {
  return researchMutation<{ freeze: unknown }>("POST", `/api/v1/research/revisions/${encodeURIComponent(revisionId)}/freeze`, body);
}
export function runFinalTest(revisionId: string, rerun: boolean) {
  return researchMutation<{ jobs: unknown }>("POST", `/api/v1/research/revisions/${encodeURIComponent(revisionId)}/final-test`, { rerun });
}
export function exportRevision(revisionId: string) {
  return researchMutation<{ path: string; files: string[] }>("POST", `/api/v1/research/revisions/${encodeURIComponent(revisionId)}/export`, {});
}
