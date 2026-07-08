export type ApiSuccess<T> = { ok: true; data: T; endpoint: string; asOf: Date };
export type ApiFailure = {
  ok: false;
  endpoint: string;
  status: number | null;
  message: string;
  body?: unknown;
  asOf: Date;
};
export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

export async function fetchApi<T>(_endpoint: string): Promise<ApiResult<T>> {
  throw new Error("not implemented");
}
