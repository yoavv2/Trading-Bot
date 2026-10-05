"use client";

import { useActivePaperStrategy } from "@/lib/useActivePaperStrategy";

/**
 * Read-only line (20.1-14, COMPAT-01): who owns paper trading right now. It has no button,
 * link, input or form -- the owner is managed through the API in this version. `none` is
 * rendered ONLY when the fetch succeeded with no owner; loading and a failed fetch render an
 * honest unknown text, never `none`.
 */
export function ActivePaperStrategyLine() {
  const active = useActivePaperStrategy();

  let text: string;
  if (active.state === "loading") {
    text = "Active paper strategy: loading";
  } else if (active.state === "unknown") {
    text = "Active paper strategy: unknown";
  } else {
    const name = active.strategyId === null ? "none" : (active.displayName ?? active.strategyId);
    text = `Active paper strategy: ${name} (managed through the API)`;
  }

  return (
    <p className="text-xs text-zinc-400" data-testid="active-paper-strategy-line">
      {text}
    </p>
  );
}
