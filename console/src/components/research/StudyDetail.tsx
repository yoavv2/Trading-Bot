"use client";

import { useState } from "react";
import Link from "next/link";
import { exportRevision, runRevision } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import type { Exposures, Progress, Readiness, Results, Revision, Study } from "@/lib/research/types";
import { isTerminalJobStatus } from "@/lib/jobStatus";
import { ReadinessPanel } from "./ReadinessPanel";
import { ProgressPanel } from "./ProgressPanel";
import { ResultsView } from "./ResultsView";
import { FinalTestPanel } from "./FinalTestPanel";

type StudyBody = { as_of: string; study: Study };
type ReadinessBody = { as_of: string; readiness: Readiness };
type ProgressBody = { as_of: string } & Progress;
type ResultsBody = { as_of: string } & Results;
type ExposuresBody = { as_of: string } & Exposures;
type RevisionBody = { as_of: string; revision: Revision };

export function StudyDetail({ studyId, initialRevisionId }: { studyId: string; initialRevisionId: string | null }) {
  const study = useApiQuery<StudyBody>(`/api/v1/research/studies/${encodeURIComponent(studyId)}`);
  const [chosen, setChosen] = useState<string | null>(initialRevisionId);
  if (!study.result) {
    return <p className="text-sm text-zinc-500">Loading…</p>;
  }
  if (!study.result.ok) {
    return <ErrorState failure={study.result} title="Study could not be loaded" />;
  }
  const revisions = study.result.data.study.revisions ?? [];
  const current = chosen && revisions.some((r) => r.revision_id === chosen) ? chosen : revisions[revisions.length - 1]?.revision_id ?? null;
  return (
    <div className="space-y-4">
      <section className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div>
            <p className="eyebrow">{study.result.data.study.kind} study</p>
            <h1 className="mt-0.5 truncate text-xl font-semibold tracking-tight text-foreground">{study.result.data.study.name}</h1>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="eyebrow mr-1">Revision</span>
          {revisions.map((revision) => (
            <button key={revision.revision_id} type="button" aria-pressed={revision.revision_id === current} onClick={() => setChosen(revision.revision_id)} className={`button-secondary min-h-8 px-2.5 py-1 text-xs ${revision.revision_id === current ? "border-accent text-accent" : ""}`}>
              {revision.revision_no}
            </button>
          ))}
          <Link href={`/research/studies/new?from=${studyId}`} className="button-secondary min-h-8 px-2.5 py-1 text-xs">
            New revision
          </Link>
          <FetchMeta asOf={study.result.asOf} loading={study.loading} onRefresh={study.refetch} />
        </div>
      </section>
      {study.result.data.study.kind === "smoke" ? (
        <p role="note" className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-xs text-amber-100">
          Smoke run: this study is an integration test of the pipeline. Its assets, dates, costs, objective and constraint are test configuration, not user-approved investment objectives or product defaults; its verdict and final-test outcome are not research claims and name no winner.
        </p>
      ) : null}
      {current ? <RevisionPanel key={current} revisionId={current} /> : <p className="text-sm text-zinc-500">No revisions.</p>}
    </div>
  );
}

function SettingsSummary({ revision }: { revision: Revision }) {
  const s = revision.settings;
  return (
    <p className="text-xs text-zinc-400">
      {s.strategy_version_ids.length} version(s) × {s.assets.length} asset(s) ({s.assets.join(", ")}); range {s.range.start} to {s.range.end}; development {s.windows.development.start}–{s.windows.development.end}, validation{" "}
      {s.windows.validation.start}–{s.windows.validation.end}, final test {s.windows.final_test.start}–{s.windows.final_test.end}; capital {s.initial_capital}, {s.quantity_policy}; costs{" "}
      {s.costs ? `${s.costs.slippage_bps} bps / ${s.costs.commission_per_order}` : "not entered"}; objective {s.objective ?? "not chosen"}, constraint {s.constraint_value ?? "not entered"}; provider {s.provider} ({s.adjusted ? "adjusted" : "raw"}).
    </p>
  );
}

function RevisionPanel({ revisionId }: { revisionId: string }) {
  const base = `/api/v1/research/revisions/${encodeURIComponent(revisionId)}`;
  const revision = useApiQuery<RevisionBody>(base);
  const readiness = useApiQuery<ReadinessBody>(`${base}/readiness`);
  const progress = useApiQuery<ProgressBody>(`${base}/progress`, {
    pollIntervalMs: 5000,
    shouldPoll: (data) => data.jobs.some((job) => !isTerminalJobStatus(job.status)),
  });
  const results = useApiQuery<ResultsBody>(`${base}/results`);
  const exposures = useApiQuery<ExposuresBody>(`${base}/exposures`);
  const capability = useMutationCapability();
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function refreshAll() {
    revision.refetch();
    readiness.refetch();
    progress.refetch();
    results.refetch();
    exposures.refetch();
  }

  async function run() {
    setBusy(true);
    setMessage(null);
    const result = await runRevision(revisionId);
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    refreshAll();
  }

  async function doExport() {
    setBusy(true);
    const result = await exportRevision(revisionId);
    setBusy(false);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    setMessage(`Exported ${result.data.files.length} file(s) to ${result.data.path}`);
    exposures.refetch();
  }

  const hasGraph = progress.result?.ok ? progress.result.data.count > 0 : false;
  // Final-test Jobs still queued or running: the panel shows progress instead of offering the
  // action again (a second request would be refused as final_test_already_run anyway).
  const finalTestInFlight = progress.result?.ok ? progress.result.data.jobs.some((job) => job.scope === "final_test" && !isTerminalJobStatus(job.status)) : false;
  const evaluated = results.result?.ok ? results.result.data.initial !== null : false;
  const canRun = capability.state === "enabled" && !busy && readiness.result?.ok === true && readiness.result.data.readiness.preflight.ready && !hasGraph;

  return (
    <div className="space-y-4">
      {readiness.result?.ok ? <ReadinessPanel readiness={readiness.result.data.readiness} summaryOnly /> : readiness.result ? <ErrorState failure={readiness.result} title="Readiness could not be checked" /> : null}
      {results.result?.ok && !evaluated ? (
        <section className="surface-panel flex flex-wrap items-center justify-between gap-3 p-4">
          <div><h2 className="text-sm font-semibold text-foreground">No evaluation yet</h2><p className="mt-1 text-xs text-muted-foreground">Initial evaluation uses the development and validation windows only.</p></div>
          <button type="button" onClick={run} disabled={!canRun} className="button-primary px-3 py-2 text-sm disabled:opacity-50">Run initial evaluation</button>
        </section>
      ) : null}
      {results.result?.ok ? (
        results.result.data.initial ? <ResultsView comparison={results.result.data.initial} /> : null
      ) : results.result ? <ErrorState failure={results.result} /> : <p className="text-sm text-muted-foreground">Loading evaluation results…</p>}
      {results.result?.ok && revision.result?.ok ? (
        <FinalTestPanel
          revisionId={revisionId}
          comparison={results.result.data.initial}
          freeze={revision.result.data.revision.freeze ?? null}
          finalTest={results.result.data.final_test}
          finalTestInFlight={finalTestInFlight}
          exposures={exposures.result?.ok ? exposures.result.data : null}
          onChanged={refreshAll}
        />
      ) : null}
      {evaluated ? <div className="flex flex-wrap items-center gap-3"><a href={`/backend${base}/report`} target="_blank" rel="noreferrer" className="button-secondary">View report</a><button type="button" onClick={doExport} disabled={capability.state !== "enabled" || busy} className="button-secondary">Export study</button></div> : null}
      {message ? <p role="status" className="text-sm">{message}</p> : null}
      <details className="surface-panel group rounded-lg">
        <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-foreground">Study settings, reports and system activity</summary>
        <div className="space-y-4 border-t border-border p-4">
      <section className="surface-panel p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-foreground">Revision settings and actions</h2>
          <FetchMeta asOf={revision.result?.asOf ?? null} loading={revision.loading} onRefresh={refreshAll} />
        </div>
        <details className="group mt-3">
          <summary className="cursor-pointer text-sm text-muted-foreground">View dates, costs and execution settings</summary>
          <div className="surface-muted mt-2 rounded-md p-3">{revision.result?.ok ? <SettingsSummary revision={revision.result.data.revision} /> : revision.result ? <ErrorState failure={revision.result} /> : null}</div>
        </details>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={run} disabled={!canRun} className="button-primary px-3 py-2 text-sm disabled:opacity-50">
            Run initial evaluation (development + validation)
          </button>
          {hasGraph ? <span className="text-xs text-zinc-500">already started for this revision</span> : null}
          {evaluated ? (
            <>
              <button type="button" onClick={doExport} disabled={capability.state !== "enabled" || busy} className="button-secondary px-3 py-2 text-sm disabled:opacity-50">
                Export (comparison.json, report, per-run files)
              </button>
              <a href={`/backend${base}/report`} target="_blank" rel="noreferrer" className="text-sm text-accent hover:underline">
                Static report (HTML)
              </a>
              <a href={`/backend${base}/report?format=md`} target="_blank" rel="noreferrer" className="text-sm text-accent hover:underline">
                Markdown
              </a>
            </>
          ) : null}
          {capability.state !== "enabled" ? <span className="text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</span> : null}
        </div>
        {message ? <p role="alert" className="mt-2 text-sm text-zinc-200">{message}</p> : null}
      </section>
      {readiness.result?.ok ? <ReadinessPanel readiness={readiness.result.data.readiness} /> : readiness.result ? <ErrorState failure={readiness.result} /> : <p className="text-sm text-muted-foreground">Loading readiness…</p>}
      {progress.result?.ok ? <ProgressPanel progress={progress.result.data} /> : progress.result ? <ErrorState failure={progress.result} /> : null}
        </div>
      </details>
    </div>
  );
}
