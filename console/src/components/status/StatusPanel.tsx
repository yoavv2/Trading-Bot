import type { ReactNode } from "react";
import type { ApiFailure, ApiResult } from "@/lib/api";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";

type StatusPanelProps<T> = {
  title: string;
  loading: boolean;
  result: ApiResult<T> | null;
  refetch: () => void;
  /**
   * Optional override for the failure branch. Used by ReadinessPanel to render a
   * degraded 503 body as data instead of a bare ErrorState. Falls back to the shared
   * <ErrorState /> when omitted.
   */
  renderError?: (failure: ApiFailure) => ReactNode;
  children: (data: T) => ReactNode;
};

/**
 * Shared chrome for the five system-status panels: title, FetchMeta as-of/refresh
 * header, and the endpoint-named error state. Deliberately scoped to this screen
 * only — phases 14-16 compose their own screens directly from the lib fetch
 * instrument and shared ErrorState/FetchMeta components, not from this wrapper.
 */
export function StatusPanel<T>({
  title,
  loading,
  result,
  refetch,
  renderError,
  children,
}: StatusPanelProps<T>) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">{title}</h2>
        <FetchMeta
          asOf={result?.asOf ?? null}
          loading={loading}
          onRefresh={refetch}
        />
      </div>
      <div className="mt-3">
        {!result ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : result.ok ? (
          children(result.data)
        ) : renderError ? (
          renderError(result)
        ) : (
          <ErrorState failure={result} />
        )}
      </div>
    </section>
  );
}
