// 20.1-14 (COMPAT-01, D-31/H-0): the old console's mutation surface does not grow. This pins
// the endpoint literals passed to postJson/putJson in non-test sources to the five existing
// mutations, and that nothing outside lib/api.ts issues a mutation.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const __dirname = dirname(fileURLToPath(import.meta.url)); // console/src/lib
const SRC_ROOT = join(__dirname, "..");

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      sources(full, out);
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

const CALL = /(?<!function\s)\b(postJson|putJson)<[^>]*>\(\s*([`"])([^`"]+)\2/g;

function normalise(endpoint: string): string {
  return endpoint.replace(/\$\{[^}]+\}/g, "{id}");
}

describe("console mutation inventory (20.1-14)", () => {
  const apiSource = readFileSync(join(SRC_ROOT, "lib", "api.ts"), "utf8");

  it("console mutation endpoints are exactly the five existing ones", () => {
    const found = new Set<string>();
    for (const match of apiSource.matchAll(CALL)) {
      const verb = match[1] === "postJson" ? "POST" : "PUT";
      found.add(`${verb} ${normalise(match[3])}`);
    }
    expect([...found].sort()).toEqual(
      [
        "POST /api/v1/jobs",
        "POST /api/v1/jobs/{id}/cancel",
        "POST /api/v1/jobs/{id}/retry",
        "PUT /api/v1/controls/kill-switch",
        "PUT /api/v1/controls/strategies/{id}",
      ].sort(),
    );
  });

  // S4: research writes go through the single `researchMutation(method, endpoint)` helper
  // in lib/api.ts; the set is pinned exactly, apart from the trading five above.
  const RESEARCH_CALL = /\bresearchMutation<[^>]*>\(\s*"(POST|PUT|DELETE)",\s*([`"])([^`"]+)\2/g;

  it("research mutation endpoints are exactly the pinned set", () => {
    const found = new Set<string>();
    for (const match of apiSource.matchAll(RESEARCH_CALL)) {
      found.add(`${match[1]} ${normalise(match[3].replace(/\$\{RS\}/g, "/api/v1/research/strategies").replace(/\$\{RC\}/g, "/api/v1/research"))}`);
    }
    expect([...found].sort()).toEqual(
      [
        "POST /api/v1/research/strategies/drafts",
        "PUT /api/v1/research/strategies/drafts/{id}",
        "DELETE /api/v1/research/strategies/drafts/{id}",
        "POST /api/v1/research/strategies/drafts/{id}/duplicate",
        "POST /api/v1/research/strategies/drafts/{id}/approve",
        "POST /api/v1/research/strategies/validate",
        "POST /api/v1/research/strategies/versions/{id}/edit",
        "POST /api/v1/research/strategies/versions/{id}/duplicate",
        "POST /api/v1/research/asset-lists",
        "PUT /api/v1/research/asset-lists/{id}",
        "DELETE /api/v1/research/asset-lists/{id}",
        "POST /api/v1/research/studies",
        "POST /api/v1/research/studies/{id}/revisions",
        "POST /api/v1/research/revisions/{id}/run",
        "POST /api/v1/research/revisions/{id}/freeze",
        "POST /api/v1/research/revisions/{id}/final-test",
        "POST /api/v1/research/revisions/{id}/export",
        // S5 assistant: one bounded request, one explicit apply (approval stays the draft route)
        "POST /api/v1/research/assistant/proposals",
        "POST /api/v1/research/assistant/proposals/{id}/apply",
      ].sort(),
    );
  });

  it("nothing outside lib/api.ts calls a mutation helper or sets a mutating method", () => {
    const violations: string[] = [];
    for (const file of sources(SRC_ROOT)) {
      const rel = relative(SRC_ROOT, file).split("\\").join("/");
      if (rel === "lib/api.ts") {
        continue;
      }
      const text = readFileSync(file, "utf8");
      if (/\b(postJson|putJson|researchMutation)\b/.test(text) || /method:\s*["'](POST|PUT|PATCH|DELETE)["']/.test(text)) {
        violations.push(rel);
      }
    }
    expect(violations).toEqual([]);
  });

  it("no source submits a paper-session Job", () => {
    const offenders: string[] = [];
    for (const file of sources(SRC_ROOT)) {
      const rel = relative(SRC_ROOT, file).split("\\").join("/");
      if (/["'`]paper-session["'`]/.test(readFileSync(file, "utf8"))) {
        offenders.push(rel);
      }
    }
    expect(offenders).toEqual([]);
  });
});
