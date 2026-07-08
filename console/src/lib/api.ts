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
