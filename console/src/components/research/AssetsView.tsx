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

/** Search the local catalog, select tickers, and manage reusable asset lists. */
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
    setSelected((current) => current.includes(ticker) ? current.filter((item) => item !== ticker) : [...current, ticker]);
  }

  function cancelEdit() {
    setEditing(null);
    setListName("");
    setSelected([]);
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
    cancelEdit();
    lists.refetch();
  }

  async function removeList(listId: string) {
    setMessage(null);
    const result = await deleteAssetList(listId);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    if (editing === listId) cancelEdit();
    lists.refetch();
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Research workspace</p>
          <h1 className="page-heading">Assets</h1>
          <p className="muted mt-1 max-w-2xl text-sm">
            Find instruments in the local catalog and save reusable selections for your studies.
          </p>
        </div>
        <FetchMeta asOf={coverage.result?.asOf ?? null} loading={coverage.loading} onRefresh={coverage.refetch} />
      </header>

      {coverage.result?.ok ? (
        <section className="surface-muted flex flex-wrap items-start gap-x-8 gap-y-3 rounded-lg p-4 text-sm" aria-label="Catalog coverage information">
          <div>
            <p className="font-medium">Catalog name coverage</p>
            <p className="muted mt-1">
              {coverage.result.data.catalog.rows_named.toLocaleString()} of {coverage.result.data.catalog.rows_total.toLocaleString()} rows have a company name.
            </p>
          </div>
          <div>
            <p className="font-medium">Last synced</p>
            <p className="muted mt-1">{coverage.result.data.catalog.last_synced_at ?? "Never"}</p>
          </div>
          <div>
            <p className="font-medium">Study limit</p>
            <p className="muted mt-1">Up to {coverage.result.data.catalog.max_assets_per_study} assets</p>
          </div>
          <p className="muted basis-full text-xs">
            {coverage.result.data.catalog.name_search_note} Refresh the catalog with a catalog-sync job. Catalog history dates describe the catalog; each asset and strategy pair still needs a readiness check.
          </p>
        </section>
      ) : coverage.result && !coverage.result.ok ? (
        <section className="surface-panel p-4">
          <ErrorState failure={coverage.result} title="Catalog information could not be loaded" />
        </section>
      ) : null}

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(360px,0.9fr)]">
        <section className="surface-panel min-w-0 p-5" aria-labelledby="catalog-search-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="catalog-search-heading" className="text-base font-semibold">Search the catalog</h2>
              <p className="muted mt-1 text-sm">Search by ticker prefix or company name.</p>
            </div>
          </div>
          <form
            className="mt-4 flex flex-col gap-2 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              setSubmitted(query.trim());
            }}
          >
            <label className="sr-only" htmlFor="catalog-query">Ticker or company name</label>
            <input
              id="catalog-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="For example, AAPL or Apple"
              className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--foreground)] placeholder:text-[var(--muted)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            />
            <button type="submit" className="button-secondary justify-center">Search</button>
          </form>

          {submitted && !search.result ? <p role="status" className="muted mt-4 text-sm">Searching the catalog…</p> : null}
          {submitted && search.result?.ok ? (
            <div className="mt-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium">{search.result.data.count} result{search.result.data.count === 1 ? "" : "s"}</p>
                <p className="muted text-xs">
                  Name search covers {search.result.data.name_coverage.rows_named.toLocaleString()} of {search.result.data.name_coverage.rows_total.toLocaleString()} catalog rows.
                </p>
              </div>
              {search.result.data.items.length === 0 ? (
                <div className="surface-muted rounded-md p-5 text-center">
                  <p className="font-medium">No matching assets</p>
                  <p className="muted mt-1 text-sm">Try a ticker prefix or a different company name.</p>
                </div>
              ) : (
                <div className="overflow-x-auto rounded-md border border-[var(--border)]">
                  <table className="w-full min-w-[650px] text-left text-sm">
                    <thead className="surface-muted text-xs">
                      <tr>
                        <th className="px-3 py-2 font-medium"><span className="sr-only">Select</span></th>
                        <th className="px-3 py-2 font-medium">Ticker</th>
                        <th className="px-3 py-2 font-medium">Name</th>
                        <th className="px-3 py-2 font-medium">Exchange</th>
                        <th className="px-3 py-2 font-medium">Type</th>
                        <th className="px-3 py-2 font-medium">Catalog history</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border)]">
                      {search.result.data.items.map((entry) => (
                        <tr key={entry.ticker} className="hover:bg-[var(--surface-muted)]">
                          <td className="px-3 py-3">
                            <input type="checkbox" aria-label={`Select ${entry.ticker}`} checked={selected.includes(entry.ticker)} onChange={() => toggle(entry.ticker)} />
                          </td>
                          <td className="px-3 py-3 font-mono font-medium">{entry.ticker}</td>
                          <td className="px-3 py-3">{entry.name ?? <span className="muted">Name unavailable; search by ticker</span>}</td>
                          <td className="muted px-3 py-3">{entry.exchange ?? "—"}</td>
                          <td className="muted px-3 py-3">{entry.asset_type ?? "—"}</td>
                          <td className="muted whitespace-nowrap px-3 py-3">{entry.catalog_start ?? "?"} to {entry.catalog_end ?? "?"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {coverage.result?.ok ? <p className="muted mt-3 text-xs">{coverage.result.data.catalog.coverage_note}</p> : null}
            </div>
          ) : null}
          {submitted && search.result && !search.result.ok ? <div className="mt-4"><ErrorState failure={search.result} /></div> : null}
        </section>

        <section className="surface-panel min-w-0 p-5" aria-labelledby="saved-lists-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="saved-lists-heading" className="text-base font-semibold">Saved asset lists</h2>
              <p className="muted mt-1 text-sm">Reuse, edit, or remove a selection.</p>
            </div>
            <FetchMeta asOf={lists.result?.asOf ?? null} loading={lists.loading} onRefresh={lists.refetch} />
          </div>

          <div className="surface-muted mt-4 rounded-md p-4">
            <p className="text-sm font-medium">{editing ? "Edit list" : "Current selection"}</p>
            <p className="muted mt-1 break-words text-xs">
              {selected.length ? `${selected.length} selected · ${selected.join(", ")}` : "No tickers selected. Choose assets from the catalog results."}
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <label className="sr-only" htmlFor="asset-list-name">List name</label>
              <input
                id="asset-list-name"
                value={listName}
                onChange={(event) => setListName(event.target.value)}
                placeholder="Name this list"
                className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--foreground)] placeholder:text-[var(--muted)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
              />
              <button type="button" onClick={saveList} disabled={!canMutate || !listName.trim()} className="button-primary justify-center disabled:cursor-not-allowed disabled:opacity-50">
                {editing ? "Save changes" : "Save list"}
              </button>
              {editing ? <button type="button" onClick={cancelEdit} className="button-secondary justify-center">Cancel</button> : null}
            </div>
            {!canMutate ? <p className="muted mt-2 text-xs">{capability.reason ?? (capability.loading ? "Checking whether list changes are enabled…" : "Mutation availability is unknown")}</p> : null}
            {message ? <p role="alert" className="mt-2 text-sm text-[var(--negative)]">{message}</p> : null}
          </div>

          {!lists.result ? (
            <p className="muted mt-4 text-sm" role="status">Loading saved lists…</p>
          ) : !lists.result.ok ? (
            <div className="mt-4"><ErrorState failure={lists.result} title="Saved asset lists could not be loaded" /></div>
          ) : lists.result.data.lists.length === 0 ? (
            <div className="surface-muted mt-4 rounded-md p-4 text-sm">
              <p className="font-medium">No saved lists yet</p>
              <p className="muted mt-1">Select assets in the catalog and save them here for your next study.</p>
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-[var(--border)]">
              {lists.result.data.lists.map((list) => (
                <li key={list.list_id} className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{list.name}</p>
                    <p className="muted mt-1 break-words font-mono text-xs">{list.tickers.join(", ") || "No tickers in this list"}</p>
                    {list.updated_at ? <p className="muted mt-1 text-xs">Updated {list.updated_at}</p> : null}
                  </div>
                  <div className="flex shrink-0 gap-3 text-sm">
                    <button
                      type="button"
                      disabled={!canMutate}
                      onClick={() => { setEditing(list.list_id); setListName(list.name); setSelected(list.tickers); setMessage(null); }}
                      className="text-[var(--accent)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                    >Edit</button>
                    <button
                      type="button"
                      disabled={!canMutate}
                      onClick={() => removeList(list.list_id)}
                      className="text-[var(--negative)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                    >Delete</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
