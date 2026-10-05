"use client";

import { JobShortcutLink } from "@/components/shortcuts/JobShortcutLink";

/**
 * /paper console shortcuts (UI-SPEC; 20.1-14 / COMPAT-01): deep-links into the /jobs/new forms
 * for the two ACCOUNT-scope paper-trading job types. A paper session is operated through the
 * API in this version, so there is no "Run paper session" shortcut -- the shared notice is
 * shown in its place.
 */
export function PaperJobShortcuts() {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="text-xs text-zinc-400">
        Paper sessions are operated through the API in this version.
      </p>
      <JobShortcutLink
        jobType="reconciliation"
        scope="account"
        label="Run reconciliation"
      />
      <JobShortcutLink
        jobType="broker-order-sync"
        scope="account"
        label="Sync broker orders"
      />
    </div>
  );
}
