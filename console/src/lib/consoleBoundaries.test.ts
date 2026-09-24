// SC6/D-17 structural enforcement — the console's equivalent of the
// Python-side AST scans in tests/test_orchestration_boundaries.py. No
// console-side boundary test existed before this plan (verified: no
// *boundary*/*enforce* files under console/src). Runs under vitest's
// default "node" environment (no DOM needed) and walks console/src with
// node:fs, deriving its root from import.meta.url rather than a relative
// "../.." guess so it is robust to where vitest resolves cwd from.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { resourceHref } from "./resourceRoutes";

const __dirname = dirname(fileURLToPath(import.meta.url)); // console/src/lib
const SRC_ROOT = join(__dirname, ".."); // console/src

function isTestFileName(name: string): boolean {
  return /\.test\.(ts|tsx)$/.test(name);
}

function walkSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const info = statSync(full);
    if (info.isDirectory()) {
      walkSourceFiles(full, out);
    } else if (/\.(ts|tsx)$/.test(entry) && !isTestFileName(entry)) {
      out.push(full);
    }
  }
  return out;
}

function toPosixRelative(absPath: string): string {
  return relative(SRC_ROOT, absPath).split("\\").join("/");
}

const ALL_SOURCE_FILES = walkSourceFiles(SRC_ROOT);

describe("SC6: no raw fetch( outside src/lib/api.ts", () => {
  it("the word-boundary-safe pattern does not false-positive on refetch(", () => {
    expect(/\bfetch\(/.test("refetch();")).toBe(false);
    expect(/\bfetch\(/.test("// a manual refetch() for the FetchMeta control")).toBe(
      false,
    );
    expect(/\bfetch\(/.test("await fetch(url)")).toBe(true);
  });

  it("no source file other than src/lib/api.ts calls fetch(", () => {
    const violations: string[] = [];
    for (const absPath of ALL_SOURCE_FILES) {
      const relPath = toPosixRelative(absPath);
      if (relPath === "lib/api.ts") {
        continue;
      }
      const content = readFileSync(absPath, "utf8");
      if (/\bfetch\(/.test(content)) {
        violations.push(relPath);
      }
    }
    expect(violations).toEqual([]);
  });
});

// Job list/detail/log/event UI scope (D-17): everything under
// components/jobs/ except the per-job-type submission forms under
// components/jobs/new/, plus the Job list page and the Job detail route
// segment. None of this exists yet (Plans 09-12) — every assertion below
// passes vacuously today and starts enforcing the moment those files land.
const JOB_UI_EXACT_FILES = ["app/jobs/page.tsx"];
const JOB_UI_INCLUDE_PREFIXES = ["components/jobs/", "app/jobs/[jobId]/"];
const JOB_UI_EXCLUDE_PREFIX = "components/jobs/new/";

function isJobUiFile(relPath: string): boolean {
  if (JOB_UI_EXACT_FILES.includes(relPath)) {
    return true;
  }
  if (relPath.startsWith(JOB_UI_EXCLUDE_PREFIX)) {
    return false;
  }
  return JOB_UI_INCLUDE_PREFIXES.some((prefix) => relPath.startsWith(prefix));
}

describe("D-17: Job list/detail/log/event UI stays job-type-agnostic", () => {
  const jobUiFiles = ALL_SOURCE_FILES.filter((absPath) =>
    isJobUiFile(toPosixRelative(absPath)),
  );

  it("no Job UI file imports the job_type -> form lookup map (lookup map 1)", () => {
    const violations: string[] = [];
    for (const absPath of jobUiFiles) {
      const content = readFileSync(absPath, "utf8");
      if (/jobTypeForms/.test(content)) {
        violations.push(toPosixRelative(absPath));
      }
    }
    expect(violations).toEqual([]);
  });

  it('no Job UI file contains the string literal "backtest"', () => {
    const violations: string[] = [];
    for (const absPath of jobUiFiles) {
      const content = readFileSync(absPath, "utf8");
      if (/["'`]backtest["'`]/.test(content)) {
        violations.push(toPosixRelative(absPath));
      }
    }
    expect(violations).toEqual([]);
  });

  it("no Job UI file branches on job_type equality or switches on it", () => {
    const equalityPattern =
      /job_type\s*(===|!==|==|!=)|(===|!==|==|!=)\s*[\w.]*job_type\b/;
    const switchPattern = /switch\s*\([^)]*job_type/;
    const violations: string[] = [];
    for (const absPath of jobUiFiles) {
      const content = readFileSync(absPath, "utf8");
      if (equalityPattern.test(content) || switchPattern.test(content)) {
        violations.push(toPosixRelative(absPath));
      }
    }
    expect(violations).toEqual([]);
  });
});

describe("Single-lookup-map discipline (D-17)", () => {
  it("JOB_TYPE_FORMS is declared only in src/lib/jobTypeForms.ts (zero occurrences allowed while that file does not exist)", () => {
    const pattern = /\b(const|let|var)\s+JOB_TYPE_FORMS\b/;
    const declaredIn: string[] = [];
    for (const absPath of ALL_SOURCE_FILES) {
      const content = readFileSync(absPath, "utf8");
      if (pattern.test(content)) {
        declaredIn.push(toPosixRelative(absPath));
      }
    }
    expect(declaredIn.every((path) => path === "lib/jobTypeForms.ts")).toBe(true);
  });

  it("RESOURCE_ROUTES is declared exactly once, in src/lib/resourceRoutes.ts", () => {
    const pattern = /\b(const|let|var)\s+RESOURCE_ROUTES\b/;
    const declaredIn: string[] = [];
    for (const absPath of ALL_SOURCE_FILES) {
      const content = readFileSync(absPath, "utf8");
      if (pattern.test(content)) {
        declaredIn.push(toPosixRelative(absPath));
      }
    }
    expect(declaredIn).toEqual(["lib/resourceRoutes.ts"]);
  });
});

describe("resourceHref (lookup map 2)", () => {
  it("resolves a strategy_run resource to /runs/<id>", () => {
    expect(resourceHref("strategy_run", "abc-123")).toBe("/runs/abc-123");
  });

  it("returns null for an unrecognized resource kind", () => {
    expect(resourceHref("future_kind", "abc-123")).toBeNull();
  });
});
