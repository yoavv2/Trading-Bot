"use client";

import Link from "next/link";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import type { Draft, Family, StrategyVersion } from "@/lib/research/types";
import { editVersion, duplicateVersion } from "@/lib/api";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { useState } from "react";

type FamiliesBody = { as_of: string; count: number; families: Family[] };
type DraftsBody = { as_of: string; count: number; drafts: Draft[] };
type VersionsBody = { as_of: string; count: number; versions: StrategyVersion[] };

export function StrategiesView({ onNavigate }: { onNavigate: (href: string) => void }) {
  const families = useApiQuery<FamiliesBody>("/api/v1/research/strategies");
  const drafts = useApiQuery<DraftsBody>("/api/v1/research/strategies/drafts");
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Research workspace</p>
          <h1 className="page-heading">Strategies</h1>
          <p className="muted mt-1 text-sm">Build ideas, review the rules, then approve a version for research.</p>
        </div>
        <Link href="/research/strategies/new" className="button-primary">
          Build a strategy
        </Link>
      </header>

      <section className="surface-panel">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Ready for studies</p>
            <h2 className="text-lg font-semibold">Approved strategies</h2>
            <p className="muted mt-1 text-sm">Approved versions are immutable. Edit one to create its next draft.</p>
          </div>
          <FetchMeta asOf={families.result?.asOf ?? null} loading={families.loading} onRefresh={families.refetch} />
        </div>
        {!families.result ? (
          <p className="muted py-6 text-sm">Loading strategies…</p>
        ) : !families.result.ok ? (
          <div className="py-3">
            <ErrorState failure={families.result} />
          </div>
        ) : families.result.data.families.length === 0 ? (
          <div className="surface-muted rounded-xl p-5">
            <p className="font-medium">No approved versions yet</p>
            <p className="muted mt-1 text-sm">Start with a plain-language idea. Review its explanation and validation before approval.</p>
            <Link href="/research/strategies/new" className="button-primary mt-4 inline-flex">Build your first strategy</Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="muted text-left text-xs uppercase tracking-wide">
              <tr>
                <th className="py-3 pr-4">Strategy</th>
                <th className="pr-4">Latest</th>
                <th className="pr-4">Versions</th>
                <th className="pr-4">Created from</th>
                <th className="pr-4">Warm-up</th>
                <th>Approved</th>
              </tr>
            </thead>
            <tbody>
              {families.result.data.families.map((family) => (
                <tr key={family.strategy_id} className="border-t border-[var(--border)]">
                  <td className="py-3 pr-4">
                    <Link href={`/research/strategies/versions/${family.latest.version_id}`} className="font-medium text-[var(--accent)] hover:underline">
                      {family.latest.name}
                    </Link>
                    <span className="muted block text-xs">{family.strategy_id.slice(0, 8)}</span>
                  </td>
                  <td className="pr-4">v{family.latest.version_no}</td>
                  <td className="pr-4">{family.version_count}</td>
                  <td className="pr-4 capitalize">{family.latest.source.replace(/_/g, " ")}</td>
                  <td className="pr-4">{family.latest.history_required} sessions</td>
                  <td className="muted">{family.latest.approved_at?.slice(0, 10) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>

      <section className="surface-panel">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">In progress</p>
            <h2 className="text-lg font-semibold">Drafts</h2>
            <p className="muted mt-1 text-sm">Drafts can be changed or deleted; approval creates a permanent version.</p>
          </div>
          <FetchMeta asOf={drafts.result?.asOf ?? null} loading={drafts.loading} onRefresh={drafts.refetch} />
        </div>
        {!drafts.result ? (
          <p className="muted py-4 text-sm">Loading drafts…</p>
        ) : !drafts.result.ok ? (
          <div className="py-3">
            <ErrorState failure={drafts.result} />
          </div>
        ) : drafts.result.data.drafts.length === 0 ? (
          <p className="surface-muted rounded-xl p-4 text-sm">No drafts yet. Describe an idea to begin.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {drafts.result.data.drafts.map((draft) => (
              <li key={draft.draft_id} className="flex flex-wrap items-center justify-between gap-2 py-3 first:pt-0 last:pb-0">
                <Link href={`/research/strategies/drafts/${draft.draft_id}`} className="font-medium text-[var(--accent)] hover:underline">
                  {draft.title}
                </Link>
                <span className="muted text-xs capitalize">{draft.source.replace(/_/g, " ")} · updated {draft.updated_at?.slice(0, 16) ?? "—"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <VersionHistory onNavigate={onNavigate} />
    </div>
  );
}

function VersionHistory({ onNavigate }: { onNavigate: (href: string) => void }) {
  const versions = useApiQuery<VersionsBody>("/api/v1/research/strategies/versions");
  const capability = useMutationCapability();
  const [message, setMessage] = useState<string | null>(null);

  async function act(kind: "edit" | "duplicate", versionId: string) {
    const result = kind === "edit" ? await editVersion(versionId) : await duplicateVersion(versionId);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    onNavigate(`/research/strategies/drafts/${(result.data as { draft: Draft }).draft.draft_id}`);
  }

  return (
    <details className="surface-panel group">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3">
        <span>
          <span className="eyebrow block">Audit trail</span>
          <span className="text-lg font-semibold">Version history</span>
          <span className="muted ml-2 text-sm">Parents, immutable hashes, and actions</span>
        </span>
        <FetchMeta asOf={versions.result?.asOf ?? null} loading={versions.loading} onRefresh={versions.refetch} />
      </summary>
      {message ? <p role="alert" className="mt-3 rounded-lg border border-[var(--negative)]/30 bg-[var(--negative)]/5 p-3 text-sm">{message}</p> : null}
      {!versions.result ? (
        <p className="muted mt-4 text-sm">Loading version history…</p>
      ) : !versions.result.ok ? (
        <div className="mt-4"><ErrorState failure={versions.result} /></div>
      ) : versions.result.data.versions.length === 0 ? (
        <p className="muted mt-4 text-sm">No approved versions to show.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="muted text-left text-xs uppercase tracking-wide">
            <tr>
              <th className="py-3 pr-4">Version</th>
              <th className="pr-4">Name</th>
              <th className="pr-4">Parent version</th>
              <th className="pr-4">Specification hash</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {versions.result.data.versions.map((version) => (
              <tr key={version.version_id} className="border-t border-[var(--border)]">
                <td className="py-3 pr-4">
                  <Link href={`/research/strategies/versions/${version.version_id}`} className="font-medium text-[var(--accent)] hover:underline">v{version.version_no}</Link>
                </td>
                <td className="pr-4">{version.name}</td>
                <td className="pr-4 font-mono text-xs">
                  {version.parent_version_id ? <Link href={`/research/strategies/versions/${version.parent_version_id}`} className="text-[var(--accent)] hover:underline">{version.parent_version_id.slice(0, 8)}</Link> : <span className="muted">Root version</span>}
                </td>
                <td className="pr-4 font-mono text-xs">{version.spec_sha256.slice(0, 12)}…</td>
                <td className="flex flex-wrap gap-2 py-2">
                  <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("edit", version.version_id)} className="button-secondary px-2 py-1 text-xs disabled:opacity-50">Edit as draft</button>
                  <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("duplicate", version.version_id)} className="button-secondary px-2 py-1 text-xs disabled:opacity-50">Duplicate</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </details>
  );
}
