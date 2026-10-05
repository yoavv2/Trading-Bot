// D-17 (P19): pins the exact set of job types with a registered submission
// form in JOB_TYPE_FORMS -- the production Python registry's INTERACTIVE types
// (20.1-14: paper-session is api_only and has no form; record-external-activity
// has never had one).

import { describe, expect, it } from "vitest";
import { JOB_TYPE_FORMS } from "./jobTypeForms";

describe("JOB_TYPE_FORMS", () => {
  it("has no paper-session key and exactly the seven other form-backed job types", () => {
    expect("paper-session" in JOB_TYPE_FORMS).toBe(false);
    expect(Object.keys(JOB_TYPE_FORMS).sort()).toEqual([
      "backtest",
      "broker-order-sync",
      "ingest-bars",
      "reconciliation",
      "risk-evaluation",
      "sync-market-sessions",
      "sync-symbol-metadata",
    ]);
  });

  it("maps every job type to a component (function)", () => {
    for (const component of Object.values(JOB_TYPE_FORMS)) {
      expect(typeof component).toBe("function");
    }
  });
});
