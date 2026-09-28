import Link from "next/link";
import { resourceHref } from "@/lib/resourceRoutes";
import { isTerminalJobStatus } from "@/lib/jobStatus";
import type { JobResource } from "../types";

type JobResourcesPanelProps = {
  resources: JobResource[];
  status: string;
};

/**
 * JOBUI-02/D-04/D-17: generic resources[] renderer. A row whose kind
 * resolves through the lookup map (2) links to its console route; any
 * other kind renders as plain `{kind}: {id}` text with no link -- this is
 * what lets a test-only kind flow through unchanged (SC6). Empty-state
 * copy branches only on Job status (terminal vs non-terminal) and
 * resources.length, never on job_type.
 */
export function JobResourcesPanel({ resources, status }: JobResourcesPanelProps) {
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <h2 className="text-sm font-semibold text-zinc-200">Linked resources</h2>
      <div className="mt-3 text-sm">
        {resources.length === 0 ? (
          <p className="text-zinc-500">
            {isTerminalJobStatus(status)
              ? "This Job produced no linked resources."
              : "No linked resources yet. This panel updates automatically if the Job's run creates one."}
          </p>
        ) : (
          <ul className="space-y-1 text-xs">
            {resources.map((resource) => {
              const href = resourceHref(resource.kind, resource.id);
              const text = `${resource.kind}: ${resource.id}`;
              return (
                <li key={`${resource.kind}:${resource.id}`} className="flex items-center gap-2">
                  {href ? (
                    <Link
                      href={href}
                      className="font-semibold text-sky-400 hover:underline"
                    >
                      {text}
                    </Link>
                  ) : (
                    <span className="text-zinc-300">{text}</span>
                  )}
                  <span className="text-zinc-500">{resource.status}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
