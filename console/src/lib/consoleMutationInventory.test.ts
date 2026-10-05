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

  it("nothing outside lib/api.ts calls a mutation helper or sets a mutating method", () => {
    const violations: string[] = [];
    for (const file of sources(SRC_ROOT)) {
      const rel = relative(SRC_ROOT, file).split("\\").join("/");
      if (rel === "lib/api.ts") {
        continue;
      }
      const text = readFileSync(file, "utf8");
      if (/\b(postJson|putJson)\b/.test(text) || /method:\s*["'](POST|PUT|PATCH|DELETE)["']/.test(text)) {
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
