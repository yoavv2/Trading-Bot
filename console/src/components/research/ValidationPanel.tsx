import type { ValidationOutcome } from "@/lib/research/types";

/**
 * The validator's findings or derived values, rendered from the backend contract. Every
 * finding shows its closed code, the YAML path it points at and the validator's message;
 * nothing is approximated or hidden. `pending` means the text changed since the last
 * validation and the shown result is for the previous text.
 */
export function ValidationPanel({ outcome, pending, example = false }: { outcome: ValidationOutcome | null; pending: boolean; example?: boolean }) {
  const valid = outcome?.valid === true;
  return (
    <section className="surface-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="eyebrow">Rule check</p>
          <h2 className="text-lg font-semibold">Validation</h2>
        </div>
        {outcome ? <span className={`badge ${valid ? "text-[var(--positive)]" : "text-[var(--negative)]"}`}>{example ? "Example only" : valid ? "Valid" : "Needs changes"}</span> : null}
        {pending ? <span className="badge text-[var(--warning)]">text changed since this result</span> : null}
      </div>
      {outcome === null ? (
        <p className="muted mt-3 text-sm">Not validated yet. Results appear as you describe or edit the strategy.</p>
      ) : outcome.valid && outcome.derived ? (
        <div className="mt-4">
          {example ? <p className="muted text-sm">This starter example is valid as written. It is not saved as your strategy; describe your idea above or edit the example rules.</p> : null}
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 text-sm">
            <dt className="muted">Warm-up history</dt>
            <dd>{outcome.derived.history_required} sessions</dd>
          </dl>
          <details className="muted mt-3 text-xs">
            <summary className="cursor-pointer">Technical validation details</summary>
            <p className="mt-2">{outcome.derived.history_required} sessions (mathematical minimum {outcome.derived.history_minimum})</p>
            <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
              <dt>Price scale</dt><dd className="capitalize">{outcome.derived.scale_class.replace(/_/g, " ")}</dd>
              <dt>Data series</dt><dd className="font-mono">{outcome.derived.terms_used.join(", ")}</dd>
              <dt>Rule operators</dt><dd className="font-mono">{outcome.derived.operators_used.join(", ")}</dd>
              <dt>Specification fingerprint</dt><dd className="font-mono">{outcome.derived.spec_sha256.slice(0, 16)}…</dd>
            </dl>
          </details>
        </div>
      ) : (
        <div>
          <p className="muted mt-3 text-sm">Review each finding, then adjust the description or advanced rules.</p>
          <ul className="mt-3 space-y-2 text-sm">
          {outcome.errors.map((error, index) => (
            <li key={`${error.code}-${error.path}-${index}`} className="rounded-xl border border-[var(--negative)]/25 bg-[var(--negative)]/5 px-3 py-2">
              <span className="font-mono text-xs font-semibold text-[var(--negative)]">{error.code}</span>
              {error.path ? <span className="muted ml-2 font-mono text-xs">at {error.path}</span> : null}
              <p className="mt-1">{error.message}</p>
            </li>
          ))}
          {outcome.errors.length === 0 ? <li className="text-[var(--negative)]">Invalid (no findings reported).</li> : null}
          </ul>
        </div>
      )}
    </section>
  );
}

export function ExplanationPanel({ explanation }: { explanation: string | null }) {
  return (
    <section className="surface-panel">
      <p className="eyebrow">Plain language</p>
      <h2 className="text-lg font-semibold">How the rules work</h2>
      <p className="muted mt-1 text-xs">Generated from the specification, not written by the assistant.</p>
      {explanation ? (
        <pre className="mt-4 whitespace-pre-wrap font-sans text-sm leading-6 text-[var(--foreground)]">{explanation}</pre>
      ) : (
        <p className="muted mt-4 text-sm">Available once the specification is valid.</p>
      )}
    </section>
  );
}
