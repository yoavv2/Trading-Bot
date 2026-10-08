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
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
          <h2 className="text-sm font-semibold text-zinc-200">Approved strategies (immutable versions)</h2>
          <div className="flex items-center gap-3">
            <Link href="/research/strategies/new" className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950">
              New strategy
            </Link>
            <FetchMeta asOf={families.result?.asOf ?? null} loading={families.loading} onRefresh={families.refetch} />
          </div>
        </div>
        {!families.result ? (
          <p className="mt-3 text-sm text-zinc-500">Loading…</p>
        ) : !families.result.ok ? (
          <div className="mt-3">
            <ErrorState failure={families.result} />
          </div>
        ) : families.result.data.families.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-500">No approved versions yet. The four example strategies appear here once seeded; your own appear after approval.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="py-1">Name</th>
                <th>Latest</th>
                <th>Versions</th>
                <th>Source</th>
                <th>History</th>
                <th>Approved</th>
              </tr>
            </thead>
            <tbody>
              {families.result.data.families.map((family) => (
                <tr key={family.strategy_id} className="border-t border-zinc-800">
                  <td className="py-1">
                    <Link href={`/research/strategies/versions/${family.latest.version_id}`} className="text-cyan-300 hover:underline">
                      {family.latest.name}
                    </Link>
                  </td>
                  <td>v{family.latest.version_no}</td>
                  <td>{family.version_count}</td>
                  <td className="text-zinc-400">{family.latest.source.replace(/_/g, " ")}</td>
                  <td className="text-zinc-400">{family.latest.history_required} sessions</td>
                  <td className="text-zinc-400">{family.latest.approved_at?.slice(0, 10) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
          <h2 className="text-sm font-semibold text-zinc-200">Drafts (editable)</h2>
          <FetchMeta asOf={drafts.result?.asOf ?? null} loading={drafts.loading} onRefresh={drafts.refetch} />
        </div>
        {!drafts.result ? (
          <p className="mt-3 text-sm text-zinc-500">Loading…</p>
        ) : !drafts.result.ok ? (
          <div className="mt-3">
            <ErrorState failure={drafts.result} />
          </div>
        ) : drafts.result.data.drafts.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-500">No drafts.</p>
        ) : (
          <ul className="mt-3 space-y-1 text-sm">
            {drafts.result.data.drafts.map((draft) => (
              <li key={draft.draft_id} className="flex items-center gap-3">
                <Link href={`/research/strategies/drafts/${draft.draft_id}`} className="text-cyan-300 hover:underline">
                  {draft.title}
                </Link>
                <span className="text-xs text-zinc-500">{draft.source.replace(/_/g, " ")} · updated {draft.updated_at?.slice(0, 16) ?? "—"}</span>
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
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Version history</h2>
        <FetchMeta asOf={versions.result?.asOf ?? null} loading={versions.loading} onRefresh={versions.refetch} />
      </div>
      {message ? <p role="alert" className="mt-2 text-sm text-red-200">{message}</p> : null}
      {!versions.result ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : !versions.result.ok ? (
        <div className="mt-3">
          <ErrorState failure={versions.result} />
        </div>
      ) : (
        <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-1">Version</th>
              <th>Name</th>
              <th>Parent</th>
              <th>Hash</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {versions.result.data.versions.map((version) => (
              <tr key={version.version_id} className="border-t border-zinc-800">
                <td className="py-1">
                  <Link href={`/research/strategies/versions/${version.version_id}`} className="text-cyan-300 hover:underline">
                    v{version.version_no}
                  </Link>
                </td>
                <td>{version.name}</td>
                <td className="font-mono text-xs text-zinc-400">
                  {version.parent_version_id ? (
                    <Link href={`/research/strategies/versions/${version.parent_version_id}`} className="hover:underline">
                      {version.parent_version_id.slice(0, 8)}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="font-mono text-xs text-zinc-400">{version.spec_sha256.slice(0, 12)}</td>
                <td className="text-right">
                  <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("edit", version.version_id)} className="mr-2 text-xs text-cyan-300 hover:underline disabled:opacity-50">
                    Edit as new draft
                  </button>
                  <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("duplicate", version.version_id)} className="text-xs text-cyan-300 hover:underline disabled:opacity-50">
                    Duplicate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </section>
  );
}
