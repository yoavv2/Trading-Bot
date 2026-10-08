// 20.1-14 (COMPAT-01, D-31/H-0): the old console gets the smallest truthful adjustments and
// NOTHING else -- no new page and no new route handler. This pins the exact set of app pages.

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

  const TRADING_PAGES = [
    "/",
    "/controls",
    "/jobs",
    "/jobs/new",
    "/jobs/[jobId]",
    "/paper",
    "/runs",
    "/runs/[runId]",
    "/strategy",
  ];

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

  it("trading console routes are exactly the nine existing pages", () => {
    const pages = files
      .filter((f) => /(^|\/)page\.tsx$/.test(f))
      .map(routeOf)
      .filter((route) => !route.startsWith("/research"))
      .sort();
    expect(pages).toEqual([...TRADING_PAGES].sort());
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
