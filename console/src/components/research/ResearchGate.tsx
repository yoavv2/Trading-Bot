"use client";

import type { ReactNode } from "react";
import { useResearchMode } from "@/lib/useResearchMode";

const START_COMMAND = "make dev";

/**
 * Chrome of every research page: the API-mode gate. Three honest outcomes:
 *   - research-mode API: the page content renders;
 *   - trading-mode API: the page says so (no research route exists there) and names
 *     the command that starts the research product;
 *   - unreachable or failing API: an actionable error instead of panels that each
 *     report their own fetch failure, with the exact failure and a retry.
 * While the first /health is in flight the content renders and each panel reports
 * its own fetch outcome.
 */
export function ResearchGate({ title, children }: { title: string; children: ReactNode }) {
  const mode = useResearchMode();
  return (
    <main className="flex-1 p-6">
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold text-zinc-100">{title}</h1>
      </div>
      {mode.state === "trading" ? (
        <div role="alert" className="rounded border border-amber-800 bg-amber-950/40 px-4 py-3 text-sm text-amber-100">
          <p className="font-semibold">This API runs in trading mode.</p>
          <p className="mt-1">
            The research product needs an API started in research mode against the research database.
            From the repository root run <code>{START_COMMAND}</code> (it starts the research API, the worker and
            this console together), then reload this page.
          </p>
        </div>
      ) : mode.failure ? (
        <div role="alert" className="rounded border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-100">
          <p className="font-semibold">
            {mode.failure.status === null
              ? "The research API is unreachable."
              : `The research API answered ${mode.failure.status} to GET /health.`}
          </p>
          <p className="mt-1">
            This console proxies <code>/backend/*</code> to the API named by <code>TRADING_CONSOLE_API_BASE_URL</code>{" "}
            (default <code>http://127.0.0.1:8000</code>). Start the research stack with <code>{START_COMMAND}</code> from
            the repository root, or point that variable at a running research API, then retry.
          </p>
          {mode.failure.message ? <p className="mt-1 font-mono text-xs text-red-200">{mode.failure.message}</p> : null}
          <button
            type="button"
            onClick={mode.refetch}
            className="mt-2 rounded border border-red-700 px-2 py-1 text-xs font-semibold hover:bg-red-900/40"
          >
            Retry
          </button>
        </div>
      ) : (
        children
      )}
    </main>
  );
}
