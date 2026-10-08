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
export function ReadinessPanel({ readiness }: { readiness: Readiness }) {
  const inputs = inputsStateCopy(readiness.inputs.state);
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="text-sm font-semibold text-zinc-200">
          Preflight{" "}
          <span className={`ml-2 rounded border px-1 text-xs ${readiness.preflight.ready ? "border-emerald-700 text-emerald-200" : "border-red-700 text-red-200"}`}>
            {readiness.preflight.ready ? "ready" : `not ready (${readiness.preflight.errors.length})`}
          </span>
        </h3>
        <p className="mt-1 text-xs text-zinc-500">
          Requirements that allow the download and the integrity check to begin. {readiness.checked.pairs} strategy/asset pair(s),{" "}
          {readiness.checked.assets} asset(s); calendar pinned at {readiness.checked.pinned_calendar_start ?? "not pinned"}.
        </p>
        {readiness.preflight.errors.length > 0 ? (
          <ul className="mt-2 space-y-1 text-sm">
            {readiness.preflight.errors.map((error, index) => (
              <li key={`${error.code}-${error.item}-${index}`} className="rounded border border-red-900 bg-red-950/40 px-2 py-1 text-red-100">
                <span className="font-mono text-xs text-red-200">{error.code}</span> <span className="font-mono text-xs text-zinc-300">{error.item}</span>
                <p>{readinessErrorCopy(error)}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-emerald-200">No preflight errors.</p>
        )}
      </section>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="text-sm font-semibold text-zinc-200">
          Inputs <span className={`ml-2 rounded border px-1 text-xs ${inputs.tone}`}>{inputs.label}</span>
        </h3>
        <p className="mt-1 text-xs text-zinc-400">{inputs.text}</p>
        <p className="mt-1 text-xs text-zinc-500">{readiness.inputs.downstream}</p>
        {readiness.inputs.attempt ? (
          <p className="mt-2 text-xs text-zinc-400">
            Latest freeze attempt: Job {readiness.inputs.attempt.job_id.slice(0, 8)} is <span className="font-semibold">{readiness.inputs.attempt.status}</span>
            {readiness.inputs.attempt.completed_at ? ` (completed ${readiness.inputs.attempt.completed_at.slice(0, 16)})` : ""}; {readiness.inputs.attempts} attempt(s) in total.
          </p>
        ) : (
          <p className="mt-2 text-xs text-zinc-500">No freeze attempt yet.</p>
        )}
        {readiness.inputs.data_freeze ? (
          <p className="mt-1 font-mono text-xs text-zinc-500">
            freeze {readiness.inputs.data_freeze.data_freeze_id.slice(0, 8)} at {readiness.inputs.data_freeze.frozen_at?.slice(0, 16)} · digest {readiness.inputs.data_freeze.input_digest.slice(0, 12)}
          </p>
        ) : null}
        {readiness.inputs.errors.length > 0 ? (
          <ul className="mt-2 space-y-1 text-sm">
            {readiness.inputs.errors.map((error, index) => (
              <li key={`${error.code}-${error.item}-${index}`} className="rounded border border-amber-900 bg-amber-950/30 px-2 py-1 text-amber-100">
                <span className="font-mono text-xs text-amber-200">{error.code}</span> <span className="font-mono text-xs text-zinc-300">{error.item}</span>
                <p>{readinessErrorCopy(error)}</p>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
