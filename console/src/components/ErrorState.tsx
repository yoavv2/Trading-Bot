import type { ApiFailure } from "@/lib/api";

type ErrorStateProps = {
  failure: ApiFailure;
  title?: string;
};

/**
 * The only approved way screens render a fetch failure (CONS-02). Always shows the
 * endpoint, the status (or "unreachable" when the network/proxy failed), and the
 * verbatim message — never an empty or fake-success render.
 */
export function ErrorState({ failure, title }: ErrorStateProps) {
  const statusLabel =
    failure.status === null ? "unreachable" : `HTTP ${failure.status}`;

  return (
    <div className="rounded border border-red-800 bg-red-950/60 px-4 py-3 text-sm text-red-100">
      <p className="font-semibold">{title ?? "Request failed"}</p>
      <p className="mt-1 font-mono text-xs text-red-200">
        {failure.endpoint} — {statusLabel}
      </p>
      <p className="mt-1 text-red-100">{failure.message}</p>
    </div>
  );
}
