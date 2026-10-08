import type { ValidationOutcome } from "@/lib/research/types";

/**
 * The validator's findings or derived values, rendered from the backend contract. Every
 * finding shows its closed code, the YAML path it points at and the validator's message;
 * nothing is approximated or hidden. `pending` means the text changed since the last
 * validation and the shown result is for the previous text.
 */
export function ValidationPanel({ outcome, pending }: { outcome: ValidationOutcome | null; pending: boolean }) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">Validation</h2>
        {pending ? <span className="text-xs text-amber-300">text changed since this result</span> : null}
      </div>
      {outcome === null ? (
        <p className="mt-2 text-sm text-zinc-500">Not validated yet.</p>
      ) : outcome.valid && outcome.derived ? (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-zinc-400">Result</dt>
          <dd className="text-emerald-300">valid</dd>
          <dt className="text-zinc-400">History required</dt>
          <dd className="text-zinc-100">
            {outcome.derived.history_required} sessions (mathematical minimum {outcome.derived.history_minimum})
          </dd>
          <dt className="text-zinc-400">Price scale</dt>
          <dd className="text-zinc-100">{outcome.derived.scale_class.replace(/_/g, " ")}</dd>
          <dt className="text-zinc-400">Terms</dt>
          <dd className="font-mono text-xs text-zinc-100">{outcome.derived.terms_used.join(", ")}</dd>
          <dt className="text-zinc-400">Operators</dt>
          <dd className="font-mono text-xs text-zinc-100">{outcome.derived.operators_used.join(", ")}</dd>
          <dt className="text-zinc-400">Specification hash</dt>
          <dd className="font-mono text-xs text-zinc-300">{outcome.derived.spec_sha256.slice(0, 16)}…</dd>
        </dl>
      ) : (
        <ul className="mt-2 space-y-1 text-sm">
          {outcome.errors.map((error, index) => (
            <li key={`${error.code}-${error.path}-${index}`} className="rounded border border-red-900 bg-red-950/40 px-2 py-1">
              <span className="font-mono text-xs text-red-200">{error.code}</span>
              {error.path ? <span className="ml-2 font-mono text-xs text-zinc-400">at {error.path}</span> : null}
              <p className="text-red-100">{error.message}</p>
            </li>
          ))}
          {outcome.errors.length === 0 ? <li className="text-red-200">invalid (no findings reported)</li> : null}
        </ul>
      )}
    </section>
  );
}

export function ExplanationPanel({ explanation }: { explanation: string | null }) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <h2 className="text-sm font-semibold text-zinc-200">Explanation (rendered from the specification)</h2>
      {explanation ? (
        <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-zinc-100">{explanation}</pre>
      ) : (
        <p className="mt-2 text-sm text-zinc-500">Available once the specification is valid.</p>
      )}
    </section>
  );
}
