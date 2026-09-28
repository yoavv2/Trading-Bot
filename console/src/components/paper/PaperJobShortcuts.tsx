"use client";

import { JobShortcutLink } from "@/components/shortcuts/JobShortcutLink";

const STRATEGY_ID = "trend_following_daily";

/**
 * /paper console shortcuts (UI-SPEC): deep-links into the prefilled
 * /jobs/new forms for the three paper-trading job types.
 */
export function PaperJobShortcuts() {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <JobShortcutLink
        jobType="paper-session"
        strategyId={STRATEGY_ID}
        label="Run paper session"
      />
      <JobShortcutLink
        jobType="reconciliation"
        strategyId={STRATEGY_ID}
        label="Run reconciliation"
      />
      <JobShortcutLink
        jobType="broker-order-sync"
        strategyId={STRATEGY_ID}
        label="Sync broker orders"
      />
    </div>
  );
}
