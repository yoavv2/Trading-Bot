import { describe, expect, it } from "vitest";
import {
  API_ONLY_NOTICE,
  catalogEntryFor,
  isApiOnly,
  outcomeToneClass,
  outcomeView,
  reasonLabel,
} from "./jobOutcome";
import type { JobOutcomeValue, JobTypeCatalogItem } from "../components/jobs/types";

const API_ONLY: JobTypeCatalogItem = {
  job_type: "session_like",
  description: "d",
  cancellation_mode: "queued_only",
  console_submission: "api_only",
};
const INTERACTIVE: JobTypeCatalogItem = {
  job_type: "probe_type",
  description: "d",
  cancellation_mode: "step_boundary",
  console_submission: "interactive",
};

const succeeded = (
  outcome: JobOutcomeValue | null | undefined,
  extra: { outcome_reason?: string | null; outcome_detail?: { failed_count?: number } | null } = {},
) => ({ status: "succeeded", outcome, ...extra });

describe("catalog helpers", () => {
  it("catalogEntryFor finds by job type and tolerates a missing catalog", () => {
    const catalog = { mutations_enabled: true, items: [API_ONLY, INTERACTIVE] };
    expect(catalogEntryFor(catalog, "probe_type")).toBe(INTERACTIVE);
    expect(catalogEntryFor(catalog, "nope")).toBeUndefined();
    expect(catalogEntryFor(null, "probe_type")).toBeUndefined();
  });

  it("isApiOnly is true only for an explicit api_only entry", () => {
    expect(isApiOnly(API_ONLY)).toBe(true);
    expect(isApiOnly(INTERACTIVE)).toBe(false);
    expect(isApiOnly({ ...INTERACTIVE, console_submission: undefined })).toBe(false);
    expect(isApiOnly(undefined)).toBe(false);
  });

  it("the notice copy is the pinned one", () => {
    expect(API_ONLY_NOTICE).toBe("Operated through the API in this version");
  });
});

describe("outcomeView", () => {
  it("renders every outcome value with its label and tone (05 section 4 examples)", () => {
    const cases: Array<[ReturnType<typeof succeeded>, string, string]> = [
      [succeeded("complete"), "Succeeded · Complete", "success"],
      [succeeded("partial"), "Succeeded · Partial", "warning"],
      [
        succeeded("paused", { outcome_reason: "working_order_commitments_unaccounted" }),
        "Succeeded · Paused: working order",
        "warning",
      ],
      [
        succeeded("requires_reevaluation", { outcome_reason: "evaluation_data_changed" }),
        "Succeeded · Re-evaluation required: evaluation data changed",
        "warning",
      ],
      [
        succeeded("terminated", { outcome_reason: "execution_window_elapsed" }),
        "Succeeded · Ended: execution window elapsed",
        "warning",
      ],
      [
        succeeded("blocked", { outcome_reason: "outcome_unresolved" }),
        "Succeeded · Blocked: outcome unresolved",
        "warning",
      ],
      [succeeded("no_action"), "Succeeded · No action", "neutral"],
      [succeeded("failed"), "Succeeded · Failed", "danger"],
    ];
    for (const [job, label, tone] of cases) {
      expect(outcomeView(job, API_ONLY)).toEqual({ label, tone });
    }
  });

  it("emerald only for complete and for no-semantics jobs", () => {
    const outcomes: JobOutcomeValue[] = [
      "partial",
      "paused",
      "requires_reevaluation",
      "terminated",
      "blocked",
      "no_action",
      "failed",
    ];
    for (const outcome of outcomes) {
      const view = outcomeView(succeeded(outcome), INTERACTIVE);
      expect(view?.tone).not.toBe("success");
      expect(outcomeToneClass(view!.tone)).not.toContain("emerald");
    }
    expect(outcomeToneClass("success")).toContain("emerald");
  });

  it("partial appends the failed count with pluralisation only when present", () => {
    expect(outcomeView(succeeded("partial", { outcome_detail: { failed_count: 1 } }), INTERACTIVE)?.label).toBe(
      "Succeeded · Partial (1 symbol failed)",
    );
    expect(outcomeView(succeeded("partial", { outcome_detail: { failed_count: 3 } }), INTERACTIVE)?.label).toBe(
      "Succeeded · Partial (3 symbols failed)",
    );
    expect(outcomeView(succeeded("partial", { outcome_detail: null }), INTERACTIVE)?.label).toBe(
      "Succeeded · Partial",
    );
  });

  it("api_only job with outcome null shows Outcome via API only (neutral), never success", () => {
    for (const outcome of [null, undefined]) {
      expect(outcomeView(succeeded(outcome), API_ONLY)).toEqual({
        label: "Outcome via API only",
        tone: "neutral",
      });
    }
  });

  it("a non-api_only (or unknown-type) legacy succeeded job stays plain Succeeded", () => {
    expect(outcomeView(succeeded(null), INTERACTIVE)).toEqual({ label: "Succeeded", tone: "success" });
    expect(outcomeView(succeeded(undefined), undefined)).toEqual({ label: "Succeeded", tone: "success" });
  });

  it("non-succeeded lifecycle statuses get no outcome view", () => {
    for (const status of ["queued", "running", "failed", "cancelled"]) {
      expect(outcomeView({ status, outcome: "paused" }, API_ONLY)).toBeNull();
    }
  });

  it("unknown reasons fall back to the raw value with underscores replaced", () => {
    expect(reasonLabel("some_new_reason")).toBe("some new reason");
    expect(reasonLabel(null)).toBeNull();
    expect(outcomeView(succeeded("paused", { outcome_reason: "some_new_reason" }), API_ONLY)?.label).toBe(
      "Succeeded · Paused: some new reason",
    );
    expect(outcomeView(succeeded("paused"), API_ONLY)?.label).toBe("Succeeded · Paused");
  });
});
