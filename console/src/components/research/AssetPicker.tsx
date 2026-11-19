"use client";

import { useEffect, useId, useRef, useState } from "react";
import { fetchApi } from "@/lib/api";
import type { AssetList, CatalogEntry, CatalogSearch } from "@/lib/research/types";
import { assetTags, looksLikeMultiple, normalizeTicker, parseTickers } from "@/lib/research/assets";
import { FEASIBILITY_COPY, coverageBadge, type AssetCoverage, type Grouped, type Removable } from "@/lib/research/preflight";

const FIELD = "mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100";
const SEARCH_LIMIT = 20;
const DEBOUNCE_MS = 200;

type SearchState =
  | { kind: "idle" }
  | { kind: "loading"; query: string }
  | { kind: "ok"; query: string; items: CatalogEntry[]; note: string; named: number; total: number }
  | { kind: "error"; query: string; message: string; status: number | null };

type Props = {
  manual: string[];
  onManualChange: (next: string[]) => void;
  listId: string;
  onListChange: (listId: string) => void;
  lists: AssetList[] | null;
  listsFailure: string | null;
  maxAssets: number | null;
  /** Per-ticker coverage from the last preflight; a ticker missing here is unknown. */
  coverage: Record<string, AssetCoverage>;
  coverageLoading: boolean;
  findings: Grouped | null;
  preflightFailure: string | null;
  removable: Removable;
  warmupSessionsMax: number | null;
};

const BADGE_TONE = { ok: "text-emerald-300", warn: "text-amber-300", unknown: "text-zinc-500" } as const;

function CoverageBadge({ ticker, coverage, loading }: { ticker: string; coverage: AssetCoverage | undefined; loading: boolean }) {
  if (!coverage || (loading && coverage.status.kind === "unknown")) {
    return (
      <span className="font-sans text-[10px] text-zinc-500" title="Coverage is being checked against the catalog">
        {loading ? "checking…" : "coverage unknown"}
      </span>
    );
  }
  const badge = coverageBadge(coverage.status);
  const status = coverage.status;
  const title =
    status.kind === "ok" || status.kind === "problem"
      ? `${ticker}${coverage.name ? ` · ${coverage.name}` : ""}: catalog ${status.catalog_start ?? "—"} → ${status.catalog_end ?? "—"}`
      : status.kind === "unknown"
        ? `${ticker}: ${status.reason}`
        : `${ticker}: not in the catalog; readiness will refuse it`;
  return (
    <span className={`font-sans text-[10px] ${BADGE_TONE[badge.tone]}`} title={title} data-testid={`coverage-${ticker}`}>
      {badge.tone === "ok" ? "✓ " : badge.tone === "warn" ? "⚠ " : "? "}
      {badge.text}
    </span>
  );
}

const FEASIBILITY_TONE: Record<string, string> = {
  covers_selected_period: "text-emerald-300",
  cannot_cover_selected_period: "text-amber-300",
  no_feasible_period: "text-red-300",
  unknown: "text-zinc-400",
};

/** Findings grouped by asset and cause; the affected versions open on demand. */
function CoverageFindings({
  findings,
  failure,
  removable,
  warmupSessionsMax,
  onRemove,
}: {
  findings: Grouped | null;
  failure: string | null;
  removable: Removable;
  warmupSessionsMax: number | null;
  onRemove: (tickers: string[]) => void;
}) {
  if (failure) {
    return (
      <p role="status" className="text-xs text-amber-200">
        Coverage check unavailable: {failure}. Every ticker is treated as unknown coverage; readiness will check again after creation.
      </p>
    );
  }
  if (!findings || (findings.byAsset.length === 0 && findings.general.length === 0)) {
    return null;
  }
  return (
    <details className="rounded border border-amber-900 bg-amber-950/30 px-3 py-2 text-xs text-amber-100" data-testid="coverage-findings" open>
      <summary className="cursor-pointer font-semibold">
        Coverage findings: {findings.byAsset.length} asset(s){findings.pairErrors > 0 ? ` (${findings.pairErrors} strategy/asset pair checks)` : ""}
        {findings.general.length > 0 ? `, ${findings.general.length} general` : ""}
      </summary>
      <ul className="mt-2 space-y-2">
        {findings.byAsset.map((asset) => (
          <li key={asset.ticker}>
            <p>
              <span className="font-mono text-amber-200">{asset.ticker}</span>
              {asset.name ? <span className="text-amber-200/70"> {asset.name}</span> : null}
              {asset.catalog_start || asset.catalog_end ? <span className="text-amber-200/70"> · catalog {asset.catalog_start ?? "—"} → {asset.catalog_end ?? "—"}</span> : null}
            </p>
            <p className={`ml-4 ${FEASIBILITY_TONE[asset.feasibility]}`} data-testid={`feasibility-${asset.ticker}`}>
              {FEASIBILITY_COPY[asset.feasibility]}
              {asset.feasibility === "no_feasible_period" ? " (not enough history after the warm-up for three windows)" : ""}
            </p>
            {asset.earliest_development_start ? (
              <p className="ml-4 text-amber-100/90" data-testid={`earliest-${asset.ticker}`}>
                Earliest development start with sufficient warm-up: <span className="font-mono">{asset.earliest_development_start}</span>
                {warmupSessionsMax !== null ? ` (longest warm-up among the selected versions: ${warmupSessionsMax} sessions)` : ""}. This date alone does not guarantee the remaining windows, data integrity or full readiness.
              </p>
            ) : asset.feasibility === "unknown" && asset.causes.every((c) => c.code !== "asset_not_in_catalog") ? (
              <p className="ml-4 text-zinc-400">Earliest development start unknown (no versions selected or the calendar could not answer).</p>
            ) : null}
            <ul className="ml-4 list-disc space-y-1">
              {asset.causes.map((cause) => (
                <li key={cause.code}>
                  <span className="font-mono text-[10px] text-amber-300">{cause.code}</span> {cause.summary}
                  {cause.versions.length > 0 ? (
                    <details className="ml-2 inline">
                      <summary className="inline cursor-pointer text-amber-300 underline">
                        {cause.versions.length} version(s)
                      </summary>
                      <ul className="ml-4 list-square text-amber-100/90">
                        {cause.versions.map((v) => (
                          <li key={v.version_id}>
                            {v.label}
                            {v.detail ? ` — ${v.detail}` : ""}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                </li>
              ))}
            </ul>
          </li>
        ))}
        {findings.general.map((error, index) => (
          <li key={`${error.code}-${index}`}>
            <span className="font-mono text-[10px] text-amber-300">{error.code}</span> {error.item}
          </li>
        ))}
      </ul>
      {removable.confirmed.length > 0 ? (
        <div className="mt-2 border-t border-amber-900/60 pt-2">
          <button type="button" onClick={() => onRemove(removable.confirmed)} className="rounded border border-amber-700 px-2 py-0.5 text-amber-100 hover:bg-amber-900/40" data-testid="remove-incompatible">
            Remove assets that cannot cover this period: {removable.confirmed.join(", ")}
          </button>
          <p className="mt-1 text-amber-200/80">
            Removes only the {removable.confirmed.length} ticker(s) with a confirmed coverage failure from this form; the saved list itself is never changed.
            {removable.unknown.length > 0 ? ` ${removable.unknown.length} ticker(s) with unknown coverage stay: ${removable.unknown.join(", ")}.` : ""}
          </p>
        </div>
      ) : null}
    </details>
  );
}

/**
 * Accessible multi-select combobox over the catalog (`/catalog/search`: ticker prefix or
 * name substring). Selected tickers are removable tags; the input stays available to add
 * more. Typing then Enter adds the highlighted result, or the typed ticker itself (readiness
 * verifies catalog coverage later); pasting "AAPL, MSFT NVDA" adds every token. A saved
 * list's tickers appear as tags marked "list": they are part of the stored snapshot exactly
 * as the backend builds it (manual first, then the list, de-duplicated), so they cannot be
 * removed one by one without detaching the list, which never edits the saved list itself.
 */
export function AssetPicker({ manual, onManualChange, listId, onListChange, lists, listsFailure, maxAssets, coverage, coverageLoading, findings, preflightFailure, removable, warmupSessionsMax }: Props) {
  const id = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<number>(-1);
  const [search, setSearch] = useState<SearchState>({ kind: "idle" });
  const [hint, setHint] = useState<string | null>(null);
  const requestRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedList = listId && lists ? (lists.find((list) => list.list_id === listId) ?? null) : null;
  const listTickers = selectedList ? selectedList.tickers : [];
  const tags = assetTags(manual, listTickers);
  const all = tags.map((tag) => tag.ticker);

  // Debounced catalog search; a stale response never overwrites a newer one. The idle
  // state is derived from the text (no setState in the effect body).
  const query = text.trim();
  const searchable = Boolean(query) && !looksLikeMultiple(query);
  useEffect(() => {
    if (!searchable) {
      requestRef.current += 1;
      return;
    }
    const requestId = ++requestRef.current;
    const timer = setTimeout(() => {
      setSearch({ kind: "loading", query });
      fetchApi<CatalogSearch>(`/api/v1/research/catalog/search?q=${encodeURIComponent(query)}&limit=${SEARCH_LIMIT}`).then((result) => {
        if (requestId !== requestRef.current) {
          return;
        }
        if (result.ok) {
          setSearch({
            kind: "ok",
            query,
            items: result.data.items,
            note: result.data.name_search_note,
            named: result.data.name_coverage.rows_named,
            total: result.data.name_coverage.rows_total,
          });
          setActive(result.data.items.length > 0 ? 0 : -1);
        } else {
          setSearch({ kind: "error", query, message: result.message, status: result.status });
          setActive(-1);
        }
      });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, searchable]);
  const view: SearchState = searchable && search.kind !== "idle" && search.query === query ? search : searchable ? { kind: "loading", query } : { kind: "idle" };

  function add(tickers: string[]) {
    const incoming = tickers.map(normalizeTicker).filter(Boolean);
    if (incoming.length === 0) {
      return;
    }
    const added: string[] = [];
    const viaList: string[] = [];
    const duplicates: string[] = [];
    const next = [...manual];
    for (const ticker of incoming) {
      if (next.includes(ticker)) {
        duplicates.push(ticker);
      } else if (listTickers.map(normalizeTicker).includes(ticker)) {
        viaList.push(ticker);
      } else {
        next.push(ticker);
        added.push(ticker);
      }
    }
    if (added.length > 0) {
      onManualChange(next);
    }
    const parts: string[] = [];
    if (added.length > 1) {
      parts.push(`Added ${added.length} tickers.`);
    }
    if (duplicates.length > 0) {
      parts.push(`Already selected: ${duplicates.join(", ")}.`);
    }
    if (viaList.length > 0) {
      parts.push(`Already included through the saved list: ${viaList.join(", ")}.`);
    }
    setHint(parts.length > 0 ? parts.join(" ") : null);
    setText("");
    setOpen(false);
    setActive(-1);
  }

  function remove(ticker: string) {
    onManualChange(manual.filter((t) => normalizeTicker(t) !== ticker));
    setHint(null);
    inputRef.current?.focus();
  }

  /**
   * Bulk removal of confirmed failures. A ticker that comes from the saved list cannot be
   * dropped while the list is referenced (the backend re-adds the list's tickers through
   * asset_list_id), so the list is detached first: its remaining tickers become manual
   * tags and the saved list is left untouched.
   */
  function removeMany(tickers: string[]) {
    const drop = new Set(tickers.map(normalizeTicker));
    const fromList = listTickers.map(normalizeTicker).filter((t) => drop.has(t));
    const keep = all.filter((t) => !drop.has(t));
    onManualChange(keep);
    if (fromList.length > 0 && listId) {
      onListChange("");
      setHint(`Removed ${tickers.join(", ")}. ${fromList.join(", ")} came from ${selectedList?.name ?? "the saved list"}, so the list was detached (its remaining tickers stay as manual tags; the saved list itself is unchanged).`);
    } else {
      setHint(`Removed ${tickers.join(", ")}.`);
    }
  }

  function detachList() {
    // Copy the snapshot into manual tags and drop the list reference; the saved list is untouched.
    onManualChange(all);
    onListChange("");
    setHint(`Detached ${selectedList?.name ?? "the list"}: its tickers are now editable here; the saved list itself is unchanged.`);
  }

  const items = view.kind === "ok" ? view.items : [];
  const listboxId = `${id}-listbox`;
  const optionId = (index: number) => `${id}-option-${index}`;

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      if (items.length > 0) {
        setActive((current) => (current + 1) % items.length);
      }
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (items.length > 0) {
        setActive((current) => (current <= 0 ? items.length - 1 : current - 1));
      }
      return;
    }
    if (event.key === "Enter" || event.key === "," || event.key === " ") {
      const typed = text.trim();
      if (event.key !== "Enter" && !typed) {
        return;
      }
      event.preventDefault();
      if (event.key === "Enter" && open && active >= 0 && active < items.length) {
        add([items[active].ticker]);
        return;
      }
      if (typed) {
        add(parseTickers(typed));
      }
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
      setActive(-1);
      return;
    }
    if (event.key === "Backspace" && text === "" && manual.length > 0) {
      event.preventDefault();
      remove(normalizeTicker(manual[manual.length - 1]));
    }
  }

  function onPaste(event: React.ClipboardEvent<HTMLInputElement>) {
    const pasted = event.clipboardData.getData("text");
    if (looksLikeMultiple(pasted)) {
      event.preventDefault();
      add(parseTickers(`${text} ${pasted}`));
    }
  }

  const overLimit = maxAssets !== null && all.length > maxAssets;
  const activeDescendant = open && active >= 0 && active < items.length ? optionId(active) : undefined;

  return (
    <div className="mt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-1.5" aria-label="Selected assets" role="list">
        {tags.length === 0 ? <span className="text-xs text-zinc-500">No assets yet.</span> : null}
        {tags.map((tag) => (
          <span
            key={tag.ticker}
            role="listitem"
            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-xs ${tag.source === "list" ? "border-violet-800 bg-violet-950/40 text-violet-100" : "border-cyan-800 bg-cyan-950/40 text-cyan-100"}`}
          >
            {tag.ticker}
            <CoverageBadge ticker={tag.ticker} coverage={coverage[tag.ticker]} loading={coverageLoading} />
            {tag.source === "list" ? (
              <span className="font-sans text-[10px] uppercase text-violet-300" title={`From saved list ${selectedList?.name ?? ""}`}>
                list
              </span>
            ) : (
              <button type="button" onClick={() => remove(tag.ticker)} aria-label={`Remove ${tag.ticker}`} className="ml-0.5 rounded px-0.5 text-cyan-300 hover:bg-cyan-900 hover:text-white">
                ×
              </button>
            )}
          </span>
        ))}
      </div>

      <div className="relative">
        <label htmlFor={`${id}-input`} className="block text-xs text-zinc-400">
          Add assets (ticker or company name)
        </label>
        <input
          ref={inputRef}
          id={`${id}-input`}
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={activeDescendant}
          aria-describedby={`${id}-help`}
          autoComplete="off"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setOpen(true);
            setHint(null);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          className={FIELD}
          placeholder="e.g. AAPL or Apple"
        />
        {open && text.trim() ? (
          <div id={listboxId} role="listbox" aria-label="Catalog matches" className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded border border-zinc-700 bg-zinc-950 text-sm shadow-lg">
            {view.kind === "loading" ? <p className="px-2 py-1.5 text-xs text-zinc-500">Searching…</p> : null}
            {view.kind === "error" ? (
              <p role="alert" className="px-2 py-1.5 text-xs text-red-200">
                Catalog search failed ({view.status ?? "unreachable"}): {view.message}. Press Enter to add &quot;{normalizeTicker(text)}&quot; unverified; readiness checks the catalog later.
              </p>
            ) : null}
            {view.kind === "ok" && items.length === 0 ? (
              <p className="px-2 py-1.5 text-xs text-zinc-500">
                No catalog match for &quot;{view.query}&quot;. Press Enter to add &quot;{normalizeTicker(text)}&quot; as typed; readiness reports it if the catalog lacks it. {view.named.toLocaleString()} of {view.total.toLocaleString()} rows have a name.
              </p>
            ) : null}
            {items.map((item, index) => {
              const already = all.includes(normalizeTicker(item.ticker));
              return (
                <div
                  key={item.ticker}
                  id={optionId(index)}
                  role="option"
                  aria-selected={index === active}
                  aria-disabled={already || undefined}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    add([item.ticker]);
                  }}
                  onMouseEnter={() => setActive(index)}
                  className={`flex cursor-pointer items-baseline justify-between gap-2 px-2 py-1 ${index === active ? "bg-zinc-800" : ""} ${already ? "opacity-50" : ""}`}
                >
                  <span>
                    <span className="font-mono text-zinc-100">{item.ticker}</span>
                    {item.name ? <span className="ml-2 text-zinc-400">{item.name}</span> : <span className="ml-2 text-zinc-600">(no name on record)</span>}
                  </span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {already ? "selected" : (item.exchange ?? "")}
                    {item.catalog_start ? ` · from ${item.catalog_start}` : ""}
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
      <details className="text-xs text-zinc-500">
        <summary id={`${id}-help`} className="cursor-pointer">↑↓ Enter to pick, paste several at once</summary>
        <p className="mt-1">Backspace on an empty field removes the last ticker. Tickers are stored upper-case with &quot;.&quot; as &quot;-&quot; (BRK.B → BRK-B). A ticker without a catalog match can still be added; readiness reports it.</p>
      </details>
      {hint ? (
        <p role="status" className="text-xs text-amber-200">
          {hint}
        </p>
      ) : null}

      <label className="block text-xs text-zinc-400">
        Saved list (optional; never edited here)
        <select value={listId} onChange={(event) => onListChange(event.target.value)} className={FIELD} disabled={lists === null && listsFailure === null}>
          <option value="">none</option>
          {lists?.map((list) => (
            <option key={list.list_id} value={list.list_id}>
              {list.name} ({list.tickers.length})
            </option>
          ))}
        </select>
      </label>
      {listsFailure ? <p className="text-xs text-amber-200">Saved lists unavailable: {listsFailure}. Manual tickers still work.</p> : null}
      {selectedList ? (
        <p className="text-xs text-zinc-400">
          <span className="text-violet-300">list</span> tags come from {selectedList.name}. To drop some,{" "}
          <button type="button" onClick={detachList} className="text-cyan-300 underline">
            detach the list and edit here
          </button>
          .
        </p>
      ) : null}
      <CoverageFindings findings={findings} failure={preflightFailure} removable={removable} warmupSessionsMax={warmupSessionsMax} onRemove={removeMany} />
      <p className="text-xs text-zinc-500">
        {all.length} asset(s){maxAssets !== null ? ` of up to ${maxAssets}` : ""}, stored exactly as shown.
        {overLimit ? <span className="text-amber-200"> Over the per-study limit; readiness will refuse the run until trimmed.</span> : null}
      </p>
    </div>
  );
}
