/**
 * UI-SPEC "StrategyStatusBadge — extracted shared component (not copied
 * markup)": the exact Phase 14 ENABLED/DISABLED badge markup, extracted
 * verbatim from StrategyOverviewPanel.tsx into one shared component so it
 * has a single owner (imported by StrategyOverviewPanel and the new
 * /controls Strategy section, per Plan 23) rather than a second
 * hand-copied element.
 */
export function StrategyStatusBadge({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={
        enabled
          ? "rounded border border-emerald-700 bg-emerald-950/60 px-2 py-0.5 text-xs font-bold tracking-wide text-emerald-300"
          : "rounded border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-xs font-bold tracking-wide text-zinc-400"
      }
    >
      {enabled ? "ENABLED" : "DISABLED"}
    </span>
  );
}
