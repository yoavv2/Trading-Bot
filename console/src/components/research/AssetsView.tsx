"use client";

import { useState } from "react";
import { createAssetList, deleteAssetList, updateAssetList } from "@/lib/api";
import { useApiQuery } from "@/lib/useApiQuery";
import { useMutationCapability } from "@/lib/useMutationCapability";
import { ErrorState } from "@/components/ErrorState";
import { FetchMeta } from "@/components/FetchMeta";
import type { AssetList, CatalogCoverage, CatalogSearch } from "@/lib/research/types";

type CoverageBody = { as_of: string; catalog: CatalogCoverage };
type ListsBody = { as_of: string; count: number; lists: AssetList[] };

/**
 * Catalog search (ticker prefix or name substring over the local catalog) with the
 * name-coverage count and note from the API, multi-select into a working selection, and
 * saved lists (create, edit tickers/name, delete). Catalog dates are shown as what the
 * provider claims; the coverage note says readiness checks the actual history per pair.
 */
export function AssetsView() {
  const coverage = useApiQuery<CoverageBody>("/api/v1/research/catalog");
  const lists = useApiQuery<ListsBody>("/api/v1/research/asset-lists");
  const capability = useMutationCapability();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [listName, setListName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const search = useApiQuery<CatalogSearch>(
    `/api/v1/research/catalog/search?q=${encodeURIComponent(submitted)}&limit=50`,
  );
  const canMutate = capability.state === "enabled";

  function toggle(ticker: string) {
    setSelected((current) => (current.includes(ticker) ? current.filter((t) => t !== ticker) : [...current, ticker]));
  }

  async function saveList() {
    setMessage(null);
    const result = editing
      ? await updateAssetList(editing, { name: listName, tickers: selected })
      : await createAssetList({ name: listName, tickers: selected });
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    setEditing(null);
    setListName("");
    setSelected([]);
    lists.refetch();
  }

  async function removeList(listId: string) {
    const result = await deleteAssetList(listId);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    lists.refetch();
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
          <h2 className="text-sm font-semibold text-zinc-200">Catalog search</h2>
          <FetchMeta asOf={coverage.result?.asOf ?? null} loading={coverage.loading} onRefresh={coverage.refetch} />
        </div>
        {coverage.result?.ok ? (
          <p className="mt-2 text-xs text-zinc-400">
            {coverage.result.data.catalog.rows_named.toLocaleString()} of {coverage.result.data.catalog.rows_total.toLocaleString()} catalog rows have a name.{" "}
            {coverage.result.data.catalog.name_search_note} Last synced {coverage.result.data.catalog.last_synced_at ?? "never"}; refresh with a <code>catalog-sync</code> Job. Up to{" "}
            {coverage.result.data.catalog.max_assets_per_study} assets per study.
          </p>
        ) : coverage.result && !coverage.result.ok ? (
          <div className="mt-2">
            <ErrorState failure={coverage.result} />
          </div>
        ) : null}
        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSubmitted(query.trim());
          }}
        >
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ticker prefix or company name" className="flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" />
          <button type="submit" className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800">
            Search
          </button>
        </form>
        {submitted && search.result?.ok ? (
          <div className="mt-3">
            <p className="text-xs text-zinc-500">
              {search.result.data.count} result(s); name search covers {search.result.data.name_coverage.rows_named} of {search.result.data.name_coverage.rows_total} rows.
            </p>
            <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-zinc-500">
                <tr>
                  <th />
                  <th>Ticker</th>
                  <th>Name</th>
                  <th>Exchange</th>
                  <th>Type</th>
                  <th>History (catalog)</th>
                </tr>
              </thead>
              <tbody>
                {search.result.data.items.map((entry) => (
                  <tr key={entry.ticker} className="border-t border-zinc-800">
                    <td>
                      <input type="checkbox" aria-label={`select ${entry.ticker}`} checked={selected.includes(entry.ticker)} onChange={() => toggle(entry.ticker)} />
                    </td>
                    <td className="font-mono">{entry.ticker}</td>
                    <td>{entry.name ?? <span className="text-zinc-500">no name (ticker search only)</span>}</td>
                    <td className="text-zinc-400">{entry.exchange}</td>
                    <td className="text-zinc-400">{entry.asset_type}</td>
                    <td className="text-zinc-400">
                      {entry.catalog_start ?? "?"} to {entry.catalog_end ?? "?"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
            {coverage.result?.ok ? <p className="mt-2 text-xs text-zinc-500">{coverage.result.data.catalog.coverage_note}</p> : null}
          </div>
        ) : submitted && search.result && !search.result.ok ? (
          <div className="mt-3">
            <ErrorState failure={search.result} />
          </div>
        ) : null}
      </section>
      <section className="rounded border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
          <h2 className="text-sm font-semibold text-zinc-200">Saved lists</h2>
          <FetchMeta asOf={lists.result?.asOf ?? null} loading={lists.loading} onRefresh={lists.refetch} />
        </div>
        <div className="mt-3 rounded border border-zinc-800 p-3">
          <p className="text-xs text-zinc-400">Selection ({selected.length}): {selected.join(", ") || "none"}</p>
          <div className="mt-2 flex gap-2">
            <input value={listName} onChange={(event) => setListName(event.target.value)} placeholder="List name" className="flex-1 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100" />
            <button type="button" onClick={saveList} disabled={!canMutate || !listName.trim()} className="rounded border border-cyan-700 px-3 py-1 text-sm text-cyan-200 hover:bg-cyan-950 disabled:opacity-50">
              {editing ? "Save changes" : "Save as list"}
            </button>
            {editing ? (
              <button type="button" onClick={() => { setEditing(null); setListName(""); setSelected([]); }} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-300">
                Cancel
              </button>
            ) : null}
          </div>
          {!canMutate ? <p className="mt-1 text-xs text-zinc-500">{capability.reason ?? "Mutation availability unknown"}</p> : null}
          {message ? <p role="alert" className="mt-1 text-sm text-red-200">{message}</p> : null}
        </div>
        {!lists.result ? (
          <p className="mt-3 text-sm text-zinc-500">Loading…</p>
        ) : !lists.result.ok ? (
          <div className="mt-3">
            <ErrorState failure={lists.result} />
          </div>
        ) : lists.result.data.lists.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-500">No saved lists.</p>
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {lists.result.data.lists.map((list) => (
              <li key={list.list_id} className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-zinc-100">{list.name}</span>
                <span className="font-mono text-xs text-zinc-400">{list.tickers.join(", ") || "(empty)"}</span>
                <button type="button" disabled={!canMutate} onClick={() => { setEditing(list.list_id); setListName(list.name); setSelected(list.tickers); }} className="text-xs text-cyan-300 hover:underline disabled:opacity-50">
                  Edit
                </button>
                <button type="button" disabled={!canMutate} onClick={() => removeList(list.list_id)} className="text-xs text-red-300 hover:underline disabled:opacity-50">
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
