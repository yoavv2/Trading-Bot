// D-17 (P19): pins the exact set of job types with a registered submission
// form in JOB_TYPE_FORMS -- the same eight types the production Python
// registry (build_default_registry) registers (test_orchestration_boundaries
// .py::test_default_registry_registers_exactly_the_phase20_job_types).

import { describe, expect, it } from "vitest";
import { JOB_TYPE_FORMS } from "./jobTypeForms";

describe("JOB_TYPE_FORMS", () => {
  it("has exactly the same eight job types as the production registry", () => {
    expect(Object.keys(JOB_TYPE_FORMS).sort()).toEqual([
      "backtest",
      "broker-order-sync",
      "ingest-bars",
      "paper-session",
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
