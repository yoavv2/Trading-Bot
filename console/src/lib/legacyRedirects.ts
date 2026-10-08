/**
 * Legacy operator-console URLs -> research pages (research pivot: the research
 * platform is the product; the trading screens are no longer served). `/jobs/*`
 * stays: the generic Job surface is infrastructure the research studies run on.
 * Applied by `next.config.ts` as permanent redirects (the trading pages are not
 * coming back); `consoleRedirects.test.ts` pins this table.
 */
export const LEGACY_REDIRECTS: ReadonlyArray<{ source: string; destination: string }> = [
  { source: "/", destination: "/research/studies" },
  { source: "/strategy", destination: "/research/strategies" },
  { source: "/runs", destination: "/research/studies" },
  { source: "/runs/:runId", destination: "/research/studies" },
  { source: "/paper", destination: "/research" },
  { source: "/controls", destination: "/research" },
];
