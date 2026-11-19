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
    return <p className="muted text-sm">Loading version…</p>;
  }
  if (!version.result.ok) {
    return <ErrorState failure={version.result} title="Version could not be loaded" />;
  }
  const v = version.result.data.version;
  return (
    <div className="space-y-4">
      <section className="surface-panel">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Approved strategy · immutable version</p>
            <h1 className="text-2xl font-semibold tracking-tight">{v.name} <span className="muted">v{v.version_no}</span></h1>
            <p className="muted mt-1 text-sm">This exact rule set is fixed. Edit it to create the next draft in its family, or duplicate it to start a new family.</p>
          </div>
          <FetchMeta asOf={version.result.asOf} loading={version.loading} onRefresh={version.refetch} />
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("edit")} className="button-primary disabled:opacity-50">
            Edit as a new draft
          </button>
          <button type="button" disabled={capability.state !== "enabled"} onClick={() => act("duplicate")} className="button-secondary disabled:opacity-50">
            Duplicate as a new strategy
          </button>
        </div>
        {message ? <p role="alert" className="mt-3 text-sm text-[var(--negative)]">{message}</p> : null}
      </section>
      <section className="surface-panel">
        <p className="eyebrow">Plain language</p>
        <h2 className="text-lg font-semibold">How the rules work</h2>
        <p className="muted mt-1 text-xs">Generated from the approved specification.</p>
        <pre className="mt-4 whitespace-pre-wrap font-sans text-sm leading-6">{v.explanation}</pre>
      </section>
      <details className="surface-panel"><summary>Version details and identifiers</summary>
        <dl className="mt-5 grid grid-cols-1 gap-x-5 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <dt className="muted">Approved</dt>
          <dd>{v.approved_at ?? "—"}</dd>
          <dt className="muted">Created from</dt>
          <dd className="capitalize">{v.source.replace(/_/g, " ")}</dd>
          <dt className="muted">Warm-up history</dt>
          <dd>{v.history_required} sessions (minimum {v.history_minimum})</dd>
          <dt className="muted">Price scale</dt>
          <dd className="capitalize">{v.scale_class.replace(/_/g, " ")}</dd>
          <dt className="muted">Family identifier</dt>
          <dd className="break-all font-mono text-xs">{v.strategy_id}</dd>
          <dt className="muted">Specification fingerprint</dt>
          <dd className="break-all font-mono text-xs">{v.spec_sha256}</dd>
          {v.assistant ? (
            <>
              <dt className="muted">Assistant record</dt>
              <dd>{v.assistant.summary}</dd>
            </>
          ) : null}
          {v.behaviour_differs_from_original ? (
            <>
              <dt className="text-[var(--warning)]">Behavior note</dt>
              <dd>{v.behaviour_differs_from_original}</dd>
            </>
          ) : null}
        </dl>
      </details>
      <details className="surface-panel">
        <summary className="cursor-pointer font-medium">Advanced: view approved specification (YAML)</summary>
        <p className="muted mt-2 text-sm">Read-only copy of the exact specification associated with this immutable version.</p>
        <pre className="surface-muted mt-3 overflow-x-auto rounded-xl p-4 font-mono text-xs leading-6">{v.yaml_text}</pre>
      </details>
      {v.assistant ? (
        <details className="surface-panel" data-testid="assistant-provenance">
          <summary className="cursor-pointer font-medium">Assistant history · {v.assistant.requests} request{v.assistant.requests === 1 ? "" : "s"}</summary>
          <p className="muted mt-2 text-sm">{v.assistant.summary}</p>
          <ol className="mt-3 space-y-2">
            {v.assistant.attempts.map((a) => (
              <li key={a.ai_draft_id} className="surface-muted rounded-lg p-3 text-xs">
                <div className="font-medium">{a.kind} · attempt {a.attempt_no} · {a.status}{a.failure_code ? ` · ${a.failure_code.replace(/_/g, " ")}` : ""}</div>
                <div className="muted mt-1">{a.provider}/{a.model} · prompt {a.prompt_version} · request {a.request_id ?? "—"} · tokens {a.input_tokens ?? "?"}/{a.output_tokens ?? "?"} · {a.completed_at ?? "—"}</div>
                <p className="mt-2 whitespace-pre-wrap">{a.user_text}</p>
                {a.note ? <p className="muted mt-1">Assistant note: {a.note}</p> : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      <details className="surface-panel">
        <summary className="cursor-pointer font-medium">Version lineage</summary>
        <p className="muted mt-2 text-sm">Versions in this family, from the original through this version.</p>
        {!lineage.result ? (
          <p className="muted mt-3 text-sm">Loading lineage…</p>
        ) : !lineage.result.ok ? (
          <div className="mt-2">
            <ErrorState failure={lineage.result} />
          </div>
        ) : (
          <ol className="mt-3 space-y-2 text-sm">
            {lineage.result.data.lineage.map((item) => (
              <li key={item.version_id} className="surface-muted rounded-lg p-3">
                <Link href={`/research/strategies/versions/${item.version_id}`} className={item.version_id === versionId ? "font-medium" : "font-medium text-[var(--accent)] hover:underline"}>
                  {item.name} v{item.version_no}
                </Link>
                <span className="muted ml-2 text-xs capitalize">{item.source.replace(/_/g, " ")} · {item.spec_sha256.slice(0, 12)}</span>
              </li>
            ))}
          </ol>
        )}
      </details>
    </div>
  );
}
