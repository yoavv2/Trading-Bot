type FetchMetaProps = {
  asOf: Date | null;
  loading: boolean;
  onRefresh: () => void;
};

/**
 * Shared as-of timestamp + manual Refresh control (CONS-03). Screens place one per
 * data panel so the operator always knows how fresh what they're looking at is, and
 * can force a re-fetch without reloading the page.
 */
export function FetchMeta({ asOf, loading, onRefresh }: FetchMetaProps) {
  const timestamp = asOf
    ? `as of ${asOf.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })}`
    : "as of —";

  return (
    <div className="flex items-center gap-2 text-xs text-zinc-400">
      <span>{timestamp}</span>
      <button
        type="button"
        onClick={onRefresh}
        disabled={loading}
        className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? "Refreshing…" : "Refresh"}
      </button>
    </div>
  );
}
