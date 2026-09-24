type AutoRefreshIndicatorProps = {
  polling: boolean;
  intervalSeconds: number;
  stoppedText: string;
};

/**
 * Shared "Auto-refreshing every {N}s" / stopped-text indicator (JOBUI-05).
 * Driven by useApiQuery's own `polling` flag -- never FetchMeta's `loading`
 * -- so background polling ticks never flicker the Refresh button (UI-SPEC
 * Polling contract). The list passes "Auto-refresh stopped — all visible
 * Jobs finished"; the detail page (Plan 12) passes "Auto-refresh stopped —
 * Job finished".
 */
export function AutoRefreshIndicator({
  polling,
  intervalSeconds,
  stoppedText,
}: AutoRefreshIndicatorProps) {
  return (
    <span className="text-xs text-zinc-500">
      {polling ? `Auto-refreshing every ${intervalSeconds}s` : stoppedText}
    </span>
  );
}
