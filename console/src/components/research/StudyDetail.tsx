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
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-zinc-200">
            {study.result.data.study.name} <span className="text-xs text-zinc-500">{study.result.data.study.kind}</span>
          </h2>
          <FetchMeta asOf={study.result.asOf} loading={study.loading} onRefresh={study.refetch} />
        </div>
        <div className="mt-2 flex flex-wrap gap-2 text-xs">
          {revisions.map((revision) => (
            <button key={revision.revision_id} type="button" onClick={() => setChosen(revision.revision_id)} className={`rounded border px-2 py-0.5 ${revision.revision_id === current ? "border-cyan-700 text-cyan-200" : "border-zinc-700 text-zinc-400"}`}>
              revision {revision.revision_no}
            </button>
          ))}
          <Link href={`/research/studies/new?from=${studyId}`} className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-300">
            new revision (new settings)
          </Link>
        </div>
      </section>
      {study.result.data.study.kind === "smoke" ? (
        <p role="note" className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-xs text-amber-100">
          Smoke run: this study is an integration test of the pipeline. Its assets, dates, costs, objective and constraint are test configuration, not user-approved investment objectives or product defaults; its verdict and final-test outcome are not research claims and name no winner.
        </p>
      ) : null}
      {current ? <RevisionPanel revisionId={current} /> : <p className="text-sm text-zinc-500">No revisions.</p>}
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
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-zinc-200">Revision settings</h3>
          <FetchMeta asOf={revision.result?.asOf ?? null} loading={revision.loading} onRefresh={refreshAll} />
        </div>
        {revision.result?.ok ? <SettingsSummary revision={revision.result.data.revision} /> : revision.result ? <ErrorState failure={revision.result} /> : null}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={run} disabled={!canRun} className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950 disabled:opacity-50">
            Run initial evaluation (development + validation)
          </button>
          {hasGraph ? <span className="text-xs text-zinc-500">already started for this revision</span> : null}
          {evaluated ? (
            <>
              <button type="button" onClick={doExport} disabled={capability.state !== "enabled" || busy} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-200 hover:bg-zinc-800 disabled:opacity-50">
                Export (comparison.json, report, per-run files)
              </button>
              <a href={`/backend${base}/report`} target="_blank" rel="noreferrer" className="text-sm text-cyan-300 hover:underline">
                Static report (HTML)
              </a>
              <a href={`/backend${base}/report?format=md`} target="_blank" rel="noreferrer" className="text-sm text-cyan-300 hover:underline">
                Markdown
              </a>
            </>
          ) : null}
          {capability.state !== "enabled" ? <span className="text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</span> : null}
        </div>
        {message ? <p role="alert" className="mt-2 text-sm text-zinc-200">{message}</p> : null}
      </section>
      {readiness.result?.ok ? <ReadinessPanel readiness={readiness.result.data.readiness} /> : readiness.result ? <ErrorState failure={readiness.result} /> : <p className="text-sm text-zinc-500">Loading readiness…</p>}
      {progress.result?.ok ? <ProgressPanel progress={progress.result.data} /> : progress.result ? <ErrorState failure={progress.result} /> : null}
      {results.result?.ok ? (
        results.result.data.initial ? (
          <ResultsView comparison={results.result.data.initial} />
        ) : (
          <p className="text-sm text-zinc-500">No evaluation yet for this revision.</p>
        )
      ) : results.result ? (
        <ErrorState failure={results.result} />
      ) : null}
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
    </div>
  );
}
