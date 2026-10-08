// 20.1-14 (COMPAT-01, D-31/H-0) pinned the old console's nine trading pages. The research
// pivot (proposal 00 Part L, plan §15) made the research platform the product: the trading
// pages (status, strategy, runs, paper, controls) are no longer served -- their URLs
// redirect (src/lib/legacyRedirects.ts) -- and the generic Job surface stays as the
// infrastructure the studies run on. This pins the exact set of app pages after that change.

import { readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const __dirname = dirname(fileURLToPath(import.meta.url)); // console/src/lib
const APP_ROOT = join(__dirname, "..", "app");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      walk(full, out);
    } else {
      out.push(relative(APP_ROOT, full).split("\\").join("/"));
    }
  }
  return out;
}

function routeOf(pagePath: string): string {
  const segments = pagePath.replace(/\/?page\.tsx$/, "");
  return segments === "" ? "/" : `/${segments}`;
}

describe("console route inventory (20.1-14)", () => {
  const files = walk(APP_ROOT).filter((f) => !/\.test\.(ts|tsx)$/.test(f));

  // The root page only redirects to the research studies page; the Job pages are the
  // generic, job-type-agnostic surface (D-17) the research Jobs are listed and submitted on.
  const INFRASTRUCTURE_PAGES = ["/", "/jobs", "/jobs/new", "/jobs/[jobId]"];

  // Frozen trading screens: no page file may come back for these without a product decision.
  const REMOVED_TRADING_PAGES = ["/controls", "/paper", "/runs", "/runs/[runId]", "/strategy"];

  // S4 (proposal Part L): the Research section is the one sanctioned addition; it is
  // visible only against a research-mode API and never touches the trading pages above.
  const RESEARCH_PAGES = [
    "/research",
    "/research/strategies",
    "/research/strategies/new",
    "/research/strategies/drafts/[draftId]",
    "/research/strategies/versions/[versionId]",
    "/research/assets",
    "/research/studies",
    "/research/studies/new",
    "/research/studies/[studyId]",
  ];

  it("non-research routes are exactly the root redirect and the generic Job pages", () => {
    const pages = files
      .filter((f) => /(^|\/)page\.tsx$/.test(f))
      .map(routeOf)
      .filter((route) => !route.startsWith("/research"))
      .sort();
    expect(pages).toEqual([...INFRASTRUCTURE_PAGES].sort());
    for (const removed of REMOVED_TRADING_PAGES) {
      expect(pages).not.toContain(removed);
    }
  });

  it("research routes are exactly the sanctioned research pages", () => {
    const pages = files
      .filter((f) => /(^|\/)page\.tsx$/.test(f))
      .map(routeOf)
      .filter((route) => route.startsWith("/research"))
      .sort();
    expect(pages).toEqual([...RESEARCH_PAGES].sort());
  });

  it("no route handler or other page-like file was added", () => {
    expect(files.filter((f) => /(^|\/)route\.(ts|tsx)$/.test(f))).toEqual([]);
    expect(files.filter((f) => /(^|\/)(page|default)\.(js|jsx|mdx)$/.test(f))).toEqual([]);
  });
});
