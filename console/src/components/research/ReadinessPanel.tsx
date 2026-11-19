import type { Readiness } from "@/lib/research/types";
import { inputsStateCopy, readinessErrorCopy } from "@/lib/research/present";

/**
 * Two verdicts, rendered from the backend contract, never merged:
 * - preflight: what must hold before ingestion and the integrity check begin (settings,
 *   versions, catalog coverage, windows, calendar);
 * - inputs: the integrity/freeze state of THIS revision's CURRENT inputs (pending, failed,
 *   stale, verified), with the attempt it comes from, so an old failure or an older freeze
 *   is never read as approval of unverified or changed inputs.
 */
export function ReadinessPanel({ readiness, summaryOnly = false }: { readiness: Readiness; summaryOnly?: boolean }) {
  const inputs = inputsStateCopy(readiness.inputs.state);
  if (summaryOnly) {
    const blockers = readiness.preflight.errors;
    const stale = readiness.inputs.state === "stale";
    const integrityNeedsAttention = readiness.inputs.state !== "verified";
    const failedInputs = readiness.inputs.state === "failed";
    const attention = stale || blockers.length > 0 || integrityNeedsAttention;
    return (
      <section className={`rounded-lg border p-3 ${attention ? failedInputs ? "border-negative/50 bg-negative/5" : "border-warning/50 bg-warning/10" : "surface-muted border-border"}`} role={stale || failedInputs ? "alert" : undefined}>
        {stale ? <p className="eyebrow text-warning">STALE · action required</p> : null}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <strong className={failedInputs ? "text-negative" : attention ? "text-warning" : "text-positive"}>{stale ? "Inputs changed after the data freeze" : failedInputs ? "Input integrity check failed" : readiness.preflight.ready ? "Preflight ready" : `Preflight blocked (${blockers.length})`}</strong>
          <span className={`text-xs ${integrityNeedsAttention ? failedInputs ? "text-negative" : "text-warning" : "text-muted-foreground"}`}>Input integrity: {inputs.label}</span>
        </div>
        {stale ? <p className="mt-1 text-xs text-muted-foreground">Changed inputs invalidate new jobs for this revision; stored results remain tied to their original frozen inputs.</p> : null}
        {blockers.length > 0 ? <ul className="mt-2 space-y-1 text-xs text-warning">{blockers.slice(0, 3).map((error, index) => <li key={`${error.code}-${error.item}-${index}`}>{readinessErrorCopy(error)}</li>)}</ul> : null}
        {blockers.length > 3 ? <p className="mt-1 text-xs text-muted-foreground">+{blockers.length - 3} more readiness blockers in the details below.</p> : null}
      </section>
    );
  }
  return (
    <div className="space-y-3">
      {readiness.inputs.state === "stale" ? (
        <section role="alert" className="rounded-lg border border-warning/50 bg-warning/10 p-4">
          <p className="eyebrow text-warning">STALE · action required</p>
          <p className="mt-1 text-sm font-medium text-foreground">The inputs changed after the data freeze. Existing results remain tied to their original frozen inputs.</p>
          <p className="mt-1 text-xs text-muted-foreground">Changed inputs invalidate new jobs for this revision. The stored results remain tied to their original frozen inputs.</p>
        </section>
      ) : null}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <section className="surface-panel p-4">
        <h3 className="text-sm font-semibold text-foreground">
          Preflight{" "}
          <span className={`badge ml-2 ${readiness.preflight.ready ? "text-positive" : "text-negative"}`}>
            {readiness.preflight.ready ? "ready" : `not ready (${readiness.preflight.errors.length})`}
          </span>
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Requirements that allow the download and the integrity check to begin. {readiness.checked.pairs} strategy/asset pair(s),{" "}
          {readiness.checked.assets} asset(s); calendar pinned at {readiness.checked.pinned_calendar_start ?? "not pinned"}.
        </p>
        {readiness.preflight.errors.length > 0 ? (
          <ul className="mt-2 space-y-1 text-sm">
            {readiness.preflight.errors.map((error, index) => (
              <li key={`${error.code}-${error.item}-${index}`} className="rounded-md border border-negative/30 bg-negative/5 px-3 py-2 text-foreground">
                <span className="font-mono text-xs text-negative">{error.code}</span> <span className="font-mono text-xs text-muted-foreground">{error.item}</span>
                <p>{readinessErrorCopy(error)}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-positive">No preflight errors.</p>
        )}
      </section>
      <section className={`surface-panel p-4 ${readiness.inputs.state === "stale" ? "border-warning/50" : ""}`}>
        <h3 className="text-sm font-semibold text-foreground">
          Data integrity <span data-state={readiness.inputs.state} className={`badge ml-2 ${readiness.inputs.state === "stale" ? "text-warning" : inputs.tone}`}>{inputs.label}</span>
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">{inputs.text}</p>
        <p className="mt-1 text-xs text-muted-foreground">{readiness.inputs.downstream}</p>
        {readiness.inputs.attempt ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Latest freeze attempt: Job {readiness.inputs.attempt.job_id.slice(0, 8)} is <span className="font-semibold">{readiness.inputs.attempt.status}</span>
            {readiness.inputs.attempt.completed_at ? ` (completed ${readiness.inputs.attempt.completed_at.slice(0, 16)})` : ""}; {readiness.inputs.attempts} attempt(s) in total.
          </p>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">No freeze attempt yet.</p>
        )}
        {readiness.inputs.data_freeze ? (
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            freeze {readiness.inputs.data_freeze.data_freeze_id.slice(0, 8)} at {readiness.inputs.data_freeze.frozen_at?.slice(0, 16)} · digest {readiness.inputs.data_freeze.input_digest.slice(0, 12)}
          </p>
        ) : null}
        {readiness.inputs.errors.length > 0 ? (
          <ul className="mt-2 space-y-1 text-sm">
            {readiness.inputs.errors.map((error, index) => (
              <li key={`${error.code}-${error.item}-${index}`} className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-foreground">
                <span className="font-mono text-xs text-warning">{error.code}</span> <span className="font-mono text-xs text-muted-foreground">{error.item}</span>
                <p>{readinessErrorCopy(error)}</p>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
      </div>
    </div>
  );
}
