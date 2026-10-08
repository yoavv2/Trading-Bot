// Asset selection helpers for the study wizard. The backend (services/research/studies.py
// `StudySettings._normalize_assets`) strips, upper-cases, turns "." into "-" and
// de-duplicates in order; and when a saved list is chosen the stored snapshot is
// `dict.fromkeys([*settings.assets, *list_tickers])`: the manual tickers first, then the
// list's, de-duplicated. These helpers reproduce both so the tags on screen are exactly
// the assets the revision will store.

/** Backend ticker normalisation. Returns "" for blank input. */
export function normalizeTicker(raw: string): string {
  return raw.trim().toUpperCase().replace(/\./g, "-");
}

/** Split pasted or typed text on commas, semicolons, whitespace and newlines. */
export function parseTickers(text: string): string[] {
  return dedupe(text.split(/[\s,;]+/).map(normalizeTicker).filter(Boolean));
}

export function dedupe(tickers: string[]): string[] {
  const out: string[] = [];
  for (const ticker of tickers) {
    if (ticker && !out.includes(ticker)) {
      out.push(ticker);
    }
  }
  return out;
}

/** Whether the text holds more than one token (bulk paste) or a separator. */
export function looksLikeMultiple(text: string): boolean {
  return /[\s,;]/.test(text.trim());
}

export type AssetTag = { ticker: string; source: "manual" | "list" };

/**
 * The exact snapshot the backend stores for `assets` + `asset_list_id`: manual tickers first
 * (normalised, de-duplicated), then the saved list's tickers that are not already there.
 */
export function mergeAssets(manual: string[], listTickers: string[]): string[] {
  return dedupe([...manual.map(normalizeTicker), ...listTickers.map(normalizeTicker)]);
}

/** The same snapshot, tagged by where each ticker comes from (manual wins on overlap). */
export function assetTags(manual: string[], listTickers: string[]): AssetTag[] {
  const manualSet = dedupe(manual.map(normalizeTicker));
  const tags: AssetTag[] = manualSet.map((ticker) => ({ ticker, source: "manual" }));
  for (const ticker of dedupe(listTickers.map(normalizeTicker))) {
    if (!manualSet.includes(ticker)) {
      tags.push({ ticker, source: "list" });
    }
  }
  return tags;
}
