"use client";

import Link from "next/link";
import { useState } from "react";
import { editVersion, duplicateVersion } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import type { Draft, StrategyVersion } from "@/lib/research/types";

type VersionBody = { as_of: string; version: StrategyVersion };
type LineageBody = { as_of: string; count: number; lineage: StrategyVersion[] };

export function VersionDetail({ versionId, onNavigate }: { versionId: string; onNavigate: (href: string) => void }) {
  const version = useApiQuery<VersionBody>(`/api/v1/research/strategies/versions/${encodeURIComponent(versionId)}`);
  const lineage = useApiQuery<LineageBody>(`/api/v1/research/strategies/versions/${encodeURIComponent(versionId)}/lineage`);
  const capability = useMutationCapability();
  const [message, setMessage] = useState<string | null>(null);

  async function act(kind: "edit" | "duplicate") {
    const result = kind === "edit" ? await editVersion(versionId) : await duplicateVersion(versionId);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    onNavigate(`/research/strategies/drafts/${(result.data as { draft: Draft }).draft.draft_id}`);
  }

  if (!version.result) {
    return <p className="text-sm text-zinc-500">Loading…</p>;
  }
  if (!version.result.ok) {
    return <ErrorState failure={version.result} title="Version could not be loaded" />;
  }
  const v = version.result.data.version;
  return (
    <div className="space-y-4">
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 pb-2">
          <h2 className="text-sm font-semibold text-zinc-200">
            {v.name} <span className="text-zinc-400">v{v.version_no}</span> <span className="ml-2 rounded border border-zinc-700 px-1 text-xs text-zinc-400">immutable</span>
          </h2>
          <FetchMeta asOf={version.result.asOf} loading={version.loading} onRefresh={version.refetch} />
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-zinc-400">Approved</dt>
          <dd>{v.approved_at ?? "—"}</dd>
          <dt className="text-zinc-400">Source</dt>
          <dd>{v.source.replace(/_/g, " ")}</dd>
          <dt className="text-zinc-400">History required</dt>
          <dd>{v.history_required} sessions (minimum {v.history_minimum})</dd>
          <dt className="text-zinc-400">Price scale</dt>
          <dd>{v.scale_class.replace(/_/g, " ")}</dd>
          <dt className="text-zinc-400">Specification hash</dt>
          <dd className="font-mono text-xs">{v.spec_sha256}</dd>
          <dt className="text-zinc-400">Family</dt>
          <dd className="font-mono text-xs">{v.strategy_id}</dd>
          {v.assistant ? (
            <>
              <dt className="text-zinc-400">Assistant</dt>
              <dd data-testid="assistant-provenance">
                {v.assistant.summary}
                <ul className="mt-1 space-y-0.5 text-xs text-zinc-400">
                  {v.assistant.attempts.map((a) => (
                    <li key={a.ai_draft_id} className="font-mono">
                      {a.kind} #{a.attempt_no} · {a.status} · {a.provider}/{a.model} · prompt {a.prompt_version} · request {a.request_id ?? "—"} · tokens {a.input_tokens ?? "?"}/{a.output_tokens ?? "?"} · {a.completed_at ?? "—"}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          ) : null}
          {v.behaviour_differs_from_original ? (
            <>
              <dt className="text-amber-300">Differs from original</dt>
              <dd className="text-amber-100">{v.behaviour_differs_from_original}</dd>
            </>
          ) : null}
        </dl>
        <div className="mt-3 flex gap-2">
          <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("edit")} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800 disabled:opacity-50">
            Edit as new draft (next version of this family)
          </button>
          <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("duplicate")} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800 disabled:opacity-50">
            Duplicate (new family)
          </button>
        </div>
        {message ? <p role="alert" className="mt-2 text-sm text-red-200">{message}</p> : null}
      </section>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
          <h2 className="text-sm font-semibold text-zinc-200">Specification (as approved)</h2>
          <pre className="mt-2 overflow-x-auto rounded bg-zinc-950 p-2 font-mono text-xs text-zinc-100">{v.yaml_text}</pre>
        </section>
        <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
          <h2 className="text-sm font-semibold text-zinc-200">Explanation</h2>
          <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-zinc-100">{v.explanation}</pre>
        </section>
      </div>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-semibold text-zinc-200">Lineage (root first)</h2>
        {!lineage.result ? (
          <p className="mt-2 text-sm text-zinc-500">Loading…</p>
        ) : !lineage.result.ok ? (
          <div className="mt-2">
            <ErrorState failure={lineage.result} />
          </div>
        ) : (
          <ol className="mt-2 space-y-1 text-sm">
            {lineage.result.data.lineage.map((item) => (
              <li key={item.version_id}>
                <Link href={`/research/strategies/versions/${item.version_id}`} className={item.version_id === versionId ? "text-zinc-100" : "text-cyan-300 hover:underline"}>
                  {item.name} v{item.version_no}
                </Link>
                <span className="ml-2 text-xs text-zinc-500">{item.source.replace(/_/g, " ")} · {item.spec_sha256.slice(0, 12)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
