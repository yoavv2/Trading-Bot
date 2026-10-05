"use client";

import Link from "next/link";
import { useMutationCapability } from "@/lib/useMutationCapability";

type JobShortcutLinkProps = {
  jobType: string;
  /** Omitted for account-scope shortcuts (no strategy_id is appended). */
  strategyId?: string;
  /** 20.1-14: `account` appends `scope=account` and never a strategy_id. */
  scope?: "account";
  label: string;
};

/**
 * Console shortcut (P19 D-18 pattern): an accent link that deep-links into
 * `/jobs/new` with the job type and strategy pre-filled. When mutations are
 * not confirmed enabled it renders a disabled accent button plus the
 * capability reason instead (same behavior the original "Run backtest"
 * shortcut had). Lives outside the job-type-agnostic Job UI scope, so passing
 * a literal job-type string from the calling screen does not touch D-17.
 * The link only pre-fills query params; the server-side spec validates every
 * field on submit.
 */
export function JobShortcutLink({
  jobType,
  strategyId,
  scope,
  label,
}: JobShortcutLinkProps) {
  const capability = useMutationCapability();
  const href =
    scope === "account"
      ? `/jobs/new?type=${encodeURIComponent(jobType)}&scope=account`
      : `/jobs/new?type=${encodeURIComponent(jobType)}&strategy_id=${encodeURIComponent(strategyId ?? "")}`;

  if (capability.state === "enabled") {
    return (
      <Link
        href={href}
        className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 hover:bg-sky-300"
      >
        {label}
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        disabled
        className="rounded bg-sky-400 px-3 py-1 text-xs font-semibold text-zinc-950 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {label}
      </button>
      {capability.reason ? (
        <span className="text-xs text-zinc-500">{capability.reason}</span>
      ) : null}
    </div>
  );
}
