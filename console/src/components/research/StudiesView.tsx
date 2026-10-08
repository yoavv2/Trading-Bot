"use client";

import Link from "next/link";
import { useApiQuery } from "@/lib/useApiQuery";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import type { Study } from "@/lib/research/types";

type StudiesBody = { as_of: string; count: number; studies: Study[] };

export function StudiesView() {
  const studies = useApiQuery<StudiesBody>("/api/v1/research/studies");
  return (
    <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
        <h2 className="text-sm font-semibold text-zinc-200">Studies</h2>
        <div className="flex items-center gap-3">
          <Link href="/research/studies/new" className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950">
            New study
          </Link>
          <FetchMeta asOf={studies.result?.asOf ?? null} loading={studies.loading} onRefresh={studies.refetch} />
        </div>
      </div>
      {!studies.result ? (
        <p className="mt-3 text-sm text-zinc-500">Loading…</p>
      ) : !studies.result.ok ? (
        <div className="mt-3">
          <ErrorState failure={studies.result} />
        </div>
      ) : studies.result.data.studies.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">No studies yet.</p>
      ) : (
        <ul className="mt-3 space-y-1 text-sm">
          {studies.result.data.studies.map((study) => (
            <li key={study.study_id}>
              <Link href={`/research/studies/${study.study_id}`} className="text-cyan-300 hover:underline">
                {study.name}
              </Link>
              <span className="ml-2 text-xs text-zinc-500">{study.kind} · created {study.created_at?.slice(0, 16) ?? "—"}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
