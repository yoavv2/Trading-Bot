"use client";

import Link from "next/link";
import { useState } from "react";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import type { AssetList, Study } from "@/lib/research/types";

type StudiesBody = { as_of: string; count: number; studies: Study[] };
type ListsBody = { as_of: string; count: number; lists: AssetList[] };
type CountBody = { as_of: string; count: number };

function formatCreatedAt(value: string | null): string {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

function studyKind(kind: string): string {
  if (kind === "substantive") return "Research study";
  if (kind === "smoke") return "Smoke test";
  return kind.replaceAll("_", " ");
}

export function StudiesView() {
  const [showAll, setShowAll] = useState(false);
  const studies = useApiQuery<StudiesBody>("/api/v1/research/studies");
  const lists = useApiQuery<ListsBody>("/api/v1/research/asset-lists");
  const strategies = useApiQuery<CountBody>("/api/v1/research/strategies");
  const drafts = useApiQuery<CountBody>("/api/v1/research/strategies/drafts");
  const studyRows = studies.result?.ok ? studies.result.data.studies : [];
  const savedStudyCount = studies.result?.ok ? studies.result.data.count : null;
  const savedListCount = lists.result?.ok ? lists.result.data.count : null;
  const strategyCount = strategies.result?.ok ? strategies.result.data.count : null;
  const draftCount = drafts.result?.ok ? drafts.result.data.count : null;
  const latestStudy = [...studyRows].sort((left, right) => {
    const timestamp = (value: string | null) => {
      const parsed = value ? Date.parse(value) : Number.NEGATIVE_INFINITY;
      return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
    };
    const leftCreated = timestamp(left.created_at);
    const rightCreated = timestamp(right.created_at);
    return rightCreated - leftCreated;
  })[0] ?? null;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Research workspace</p>
          <h1 className="page-heading">Your research, at a glance.</h1>
          <p className="muted mt-1 max-w-2xl text-sm">
            Compare strategy versions on selected assets, then review the evidence and limitations.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <FetchMeta asOf={studies.result?.asOf ?? null} loading={studies.loading} onRefresh={() => { studies.refetch(); lists.refetch(); strategies.refetch(); drafts.refetch(); }} />
          {latestStudy ? (
            <Link href={`/research/studies/${latestStudy.study_id}`} className="button-secondary">
              Continue latest study
            </Link>
          ) : null}
          <Link href="/research/studies/new" className="button-primary">
            New study
          </Link>
        </div>
      </header>

      <section className="metric-grid" aria-label="Research workspace counts">
        <article className="metric-card">
          <p className="muted text-sm">Studies</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">{savedStudyCount ?? "—"}</p>
          <p className="muted mt-1 text-xs">Saved research studies</p>
        </article>
        <article className="metric-card">
          <p className="muted text-sm">Saved asset lists</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">{savedListCount ?? "—"}</p>
          <p className="muted mt-1 text-xs">Reusable selections for new studies</p>
        </article>
        <article className="metric-card">
          <p className="muted text-sm">Approved strategies</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">{strategyCount ?? "—"}</p>
          <p className="muted mt-1 text-xs">Strategy families ready for studies</p>
        </article>
        <article className="metric-card">
          <p className="muted text-sm">Strategy drafts</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">{draftCount ?? "—"}</p>
          <p className="muted mt-1 text-xs">Ideas still in draft</p>
        </article>
      </section>

      {lists.result && !lists.result.ok ? (
        <section className="surface-panel p-4" aria-label="Asset list count unavailable">
          <ErrorState failure={lists.result} title="Saved asset lists could not be loaded" />
        </section>
      ) : null}
      {strategies.result && !strategies.result.ok ? (
        <section className="surface-panel p-4" aria-label="Approved strategy count unavailable">
          <ErrorState failure={strategies.result} title="Approved strategy count could not be loaded" />
        </section>
      ) : null}
      {drafts.result && !drafts.result.ok ? (
        <section className="surface-panel p-4" aria-label="Strategy draft count unavailable">
          <ErrorState failure={drafts.result} title="Strategy draft count could not be loaded" />
        </section>
      ) : null}

      <section className="surface-panel overflow-hidden" aria-labelledby="recent-studies-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div>
            <h2 id="recent-studies-heading" className="text-base font-semibold">Recent studies</h2>
            <p className="muted mt-1 text-sm">Open a study to review its revisions, readiness, and results.</p>
          </div>
          <Link href="/research/assets" className="button-secondary">Manage assets</Link>
        </div>

        {!studies.result ? (
          <p className="muted px-5 py-8 text-sm" role="status">Loading studies…</p>
        ) : !studies.result.ok ? (
          <div className="p-5">
            <ErrorState failure={studies.result} title="Studies could not be loaded" />
          </div>
        ) : studyRows.length === 0 ? (
          <div className="px-5 py-10 text-center">
            <h3 className="text-lg font-semibold">Start with a research question</h3>
            <p className="muted mx-auto mt-2 max-w-xl text-sm">
              Create a study to choose assets, strategy versions, a time range, and the conditions you want to evaluate.
            </p>
            <Link href="/research/studies/new" className="button-primary mt-5">Create your first study</Link>
            <p className="muted mt-4 text-xs">
              Need a reusable asset selection first? <Link className="text-[var(--accent)] hover:underline" href="/research/assets">Build an asset list</Link>.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-[var(--border)]">
            {(showAll ? studyRows : studyRows.slice(0, 5)).map((study) => (
              <Link
                key={study.study_id}
                href={`/research/studies/${study.study_id}`}
                className="group flex flex-wrap items-center justify-between gap-3 px-5 py-4 transition-colors hover:bg-[var(--surface-muted)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent)]"
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium group-hover:text-[var(--accent)]">{study.name}</span>
                  <span className="muted mt-1 block text-xs">{studyKind(study.kind)} · Created {formatCreatedAt(study.created_at)}</span>
                </span>
                <span className="badge">Open study <span aria-hidden="true">→</span></span>
              </Link>
            ))}
            {studyRows.length > 5 ? <div className="px-5 py-3"><button type="button" className="button-secondary" onClick={() => setShowAll((current) => !current)}>{showAll ? "Show recent studies" : `Show all ${studyRows.length} studies`}</button></div> : null}
          </div>
        )}
      </section>
    </div>
  );
}
