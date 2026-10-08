// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ValidationPanel } from "./ValidationPanel";
import { ReadinessPanel } from "./ReadinessPanel";
import { ResultsView } from "./ResultsView";
import { FinalTestPanel } from "./FinalTestPanel";
import { DraftEditor } from "./DraftEditor";
import { KillSwitchBannerForMode, ResearchGate } from "./ResearchGate";
import { wizardToSettings, settingsToWizard, groupVersions, EMPTY_WIZARD } from "./StudyWizard";
import type { Candidate, Comparison, FinalTestBlock, Metrics, Readiness } from "@/lib/research/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Fixtures (shapes of the backend contract)
// ---------------------------------------------------------------------------

const METRICS: Metrics = {
  net_total_return: 0.12,
  cagr: 0.1,
  max_drawdown: -0.08,
  drawdown_duration: 12,
  closed_trades: 14,
  open_at_end: { count: 1, unrealized_pnl: 10 },
  holding_period: { median_sessions: 4, mean_sessions: 5.2 },
  win_rate: 0.5,
  profit_factor: null,
  expectancy: 12.5,
  sharpe_daily_ann: 1.1,
  sortino_daily_ann: null,
  exposure: 0.4,
  turnover: 3.2,
  total_costs: { currency: 120, slippage: 100, commission: 20, pct_of_initial_capital: 0.0012 },
  rounding_slack: 0.001,
  skipped_fills: 0,
  zero_quantity_fills: 0,
  flags: [],
  notes: { profit_factor: "undefined: no losing trades", sortino_daily_ann: "undefined: no negative day" },
};

function candidate(overrides: Partial<Candidate>): Candidate {
  const window = {
    run_id: "run-1",
    metrics: METRICS,
    evidence: {
      grade: "meets_predefined_study_conditions",
      grade_reasons: [],
      descriptors: {
        closed_trades: 14,
        open_at_end: 1,
        measured_sessions: 180,
        median_holding_sessions: 4,
        holding_ratio: 45,
        clusters: 11,
        cluster_span_sessions: 5,
        concentration: { best_trade_share: 0.2, best_three_share: 0.4, best_month_share: 0.3 },
      },
      limitations: ["No grade certifies statistical reliability."],
    },
    excess_return_vs_benchmark: 0.02,
  };
  return {
    strategy_version_id: "11111111-aaaa",
    strategy_name: "Fast cross",
    version_no: 1,
    asset: "AAA",
    status: "eligible",
    status_reasons: [],
    rank: 1,
    co_leading: false,
    windows: { development: window, validation: window },
    benchmark: { development: { ...METRICS, run_id: "b1" }, validation: { ...METRICS, net_total_return: 0.1, run_id: "b2" } },
    ...overrides,
  };
}

function comparison(verdict: string, candidates: Candidate[]): Comparison {
  const ranking = candidates
    .filter((c) => c.status === "eligible")
    .map((c, index) => ({
      rank: index + 1,
      strategy_version_id: c.strategy_version_id,
      asset: c.asset,
      co_leading: c.co_leading,
      net_total_return: c.windows.validation?.metrics.net_total_return ?? null,
      max_drawdown: c.windows.validation?.metrics.max_drawdown ?? null,
      cagr: c.windows.validation?.metrics.cagr ?? null,
      excess_return_vs_benchmark: 0.02,
      grade: c.windows.validation?.evidence.grade ?? "insufficient",
    }));
  const labels: Record<string, string> = {
    leading_candidate_identified: "leading candidate under these criteria",
    insufficient_evidence: "insufficient evidence",
    no_candidate_qualifies: "no candidate qualifies",
  };
  return {
    schema_version: 1,
    study: { study_id: "s", name: "Study", kind: "substantive" },
    revision: { revision_id: "rev-1", revision_no: 1 },
    settings: {
      mode: "single_asset_independent",
      strategy_version_ids: ["11111111-aaaa"],
      assets: ["AAA"],
      asset_list_id: null,
      range: { start: "2016-01-04", end: "2019-12-31" },
      windows: { development: { start: "2017-01-03", end: "2018-06-29" }, validation: { start: "2018-07-02", end: "2019-03-29" }, final_test: { start: "2019-04-01", end: "2019-12-31" } },
      initial_capital: "100000",
      quantity_policy: "fractional",
      costs: { slippage_bps: "5", commission_per_order: "1" },
      objective: "return_first",
      constraint_value: "0.2",
      provider: "tiingo",
      adjusted: true,
      note: "",
    },
    mode_label: "independent tests, one asset each; not a portfolio",
    windows: { development: { start: "2017-01-03", end: "2018-06-29" }, validation: { start: "2018-07-02", end: "2019-03-29" }, final_test: { start: "2019-04-01", end: "2019-12-31" } },
    windows_evaluated: ["development", "validation"],
    data: { provider: "tiingo", adjusted: true, data_freeze_id: "freeze-1", input_digest: "abcdef1234567890", calendar_start: "2014-01-02" },
    code_sha: "deadbeefcafe",
    candidates,
    ranking: {
      objective: "return_first",
      constraint_value: 0.2,
      ranking_key: "net_total_return desc, then max_drawdown desc (closer to zero first), then co_leading",
      ranking,
      co_leaders: ranking.filter((r) => r.co_leading),
      verdict,
      verdict_label: labels[verdict],
      profit_factor_affects_order: false,
    },
    verdict,
    verdict_label: labels[verdict],
    benchmark_note: "Buy-and-hold of the same asset, shown for comparison only: it is never a selection gate.",
    final_test: { state: "not_frozen", note: "No final-test run exists for this revision." },
    limitations: ["Only research done inside this application is recorded."],
    evidence_limitations: ["No grade certifies statistical reliability."],
  };
}

const READINESS: Readiness = {
  revision_id: "rev-1",
  ready: false,
  status: "not_ready",
  preflight: {
    ready: false,
    errors: [
      { code: "costs_missing", item: "costs" },
      { code: "asset_not_in_catalog", item: "ZZZ" },
      { code: "coverage_start_too_late", item: "v:AAA", required_start: "2016-03-01", catalog_start: "2018-01-02" },
    ],
  },
  inputs: { state: "pending", verified: false, errors: [], attempt: null, attempts: 0, data_freeze: null, downstream: "backtests and evaluation run only after a verified freeze" },
  errors: [],
  checked: { pairs: 2, assets: 2, strategy_versions: 1, required_start_by_pair: {}, pinned_calendar_start: "2014-01-02" },
};

// ---------------------------------------------------------------------------
// Validation and explanation
// ---------------------------------------------------------------------------

describe("ValidationPanel", () => {
  it("lists every finding with its code, path and message", () => {
    render(
      <ValidationPanel
        pending={false}
        outcome={{ valid: false, errors: [{ code: "undefined_reference", path: "entry.all_of[0].right", message: "'nope' is not a series or indicator" }, { code: "unknown_field", path: "stop_loss", message: "extra" }], derived: null, explanation: null }}
      />,
    );
    expect(screen.getByText("undefined_reference")).toBeTruthy();
    expect(screen.getByText("at entry.all_of[0].right")).toBeTruthy();
    expect(screen.getByText("'nope' is not a series or indicator")).toBeTruthy();
    expect(screen.getByText("unknown_field")).toBeTruthy();
  });

  it("shows derived values when valid and flags a stale result", () => {
    render(
      <ValidationPanel
        pending
        outcome={{ valid: true, errors: [], derived: { name: "x", spec_sha256: "a".repeat(64), history_required: 200, history_minimum: 200, scale_class: "price_scale_free", terms_used: ["close"], operators_used: ["gt"] }, explanation: "Enter long when" }}
      />,
    );
    expect(screen.getByText("200 sessions (mathematical minimum 200)")).toBeTruthy();
    expect(screen.getByText("text changed since this result")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Readiness: preflight vs inputs, every state
// ---------------------------------------------------------------------------

describe("ReadinessPanel", () => {
  it("explains the market-sessions gap with the Job to submit", () => {
    render(<ReadinessPanel readiness={{ ...READINESS, preflight: { ready: false, errors: [{ code: "market_sessions_not_synced", item: "market_sessions", from_date: "2016-12-20", to_date: "2019-12-31", missing_sessions: 755 }] } }} />);
    expect(screen.getByText("market_sessions_not_synced")).toBeTruthy();
    expect(screen.getByText(/submit a sync-market-sessions Job/)).toBeTruthy();
    expect(screen.getByText(/missing_sessions: 755/)).toBeTruthy();
  });

  it("shows every preflight error and a pending inputs state that approves nothing", () => {
    render(<ReadinessPanel readiness={READINESS} />);
    expect(screen.getByText("not ready (3)")).toBeTruthy();
    expect(screen.getByText("costs_missing")).toBeTruthy();
    expect(screen.getByText("asset_not_in_catalog")).toBeTruthy();
    expect(screen.getByText(/Catalog history starts after the warm-up/)).toBeTruthy();
    expect(screen.getByText("Not verified")).toBeTruthy();
    expect(screen.getByText(/Nothing here approves the inputs/)).toBeTruthy();
  });

  it.each([
    ["failed", "Failed", /Every backtest and evaluation of that graph was cancelled/],
    ["stale", "Stale", /new Jobs for this revision refuse to run/],
    ["verified", "Verified", /passed the integrity check and have not changed since/],
  ] as const)("renders the %s inputs state with its attempt", (state, label, text) => {
    const readiness: Readiness = {
      ...READINESS,
      preflight: { ready: true, errors: [] },
      inputs: {
        state,
        verified: state === "verified",
        errors: state === "failed" ? [{ code: "integrity_error", item: "BBB", finding: "ohlc_relation", session_date: "2017-05-01" }] : state === "stale" ? [{ code: "inputs_changed_after_freeze", item: "f", reason: "1 bar row(s) changed after the freeze" }] : [],
        attempt: { job_id: "job-12345678", status: state === "failed" ? "failed" : "succeeded", completed_at: "2026-10-07T20:00:00Z", failure_message: null, link: "/api/v1/jobs/job-12345678" },
        attempts: 1,
        data_freeze: state === "failed" ? null : { data_freeze_id: "freeze-12345678", frozen_at: "2026-10-07T20:00:00Z", input_digest: "0123456789abcdef", inputs_path: null },
        downstream: "x",
      },
    };
    render(<ReadinessPanel readiness={readiness} />);
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.getByText(/Latest freeze attempt: Job job-1234/)).toBeTruthy();
    if (state === "failed") {
      expect(screen.getByText("integrity_error")).toBeTruthy();
    }
    if (state === "stale") {
      expect(screen.getByText(/1 bar row\(s\) changed after the freeze/)).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// Results for every verdict; final test for every outcome
// ---------------------------------------------------------------------------

describe("ResultsView", () => {
  it("renders a leading candidate with return and risk kept apart and the benchmark beside", () => {
    render(<ResultsView comparison={comparison("leading_candidate_identified", [candidate({})])} />);
    expect(screen.getByText("leading candidate under these criteria")).toBeTruthy();
    expect(screen.getByText("independent tests, one asset each; not a portfolio")).toBeTruthy();
    expect(screen.getAllByText("Return").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Risk").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Buy-and-hold").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Excess return vs benchmark: 2.00%/).length).toBe(2);
    expect(screen.getAllByText(/undefined: no losing trades/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Profit factor affects the order: false/)).toBeTruthy();
    // The ranking row identifies the version by name and number, not only by id prefix.
    expect(screen.getAllByText(/Fast cross v1/).length).toBeGreaterThan(0);
  });

  it("renders insufficient evidence and no-candidate verdicts without forcing a winner", () => {
    const insufficient = candidate({ status: "insufficient_evidence", status_reasons: ["no closed trades"], rank: null });
    const { unmount } = render(<ResultsView comparison={comparison("insufficient_evidence", [insufficient])} />);
    expect(screen.getAllByText("insufficient evidence").length).toBeGreaterThan(0);
    expect(screen.getByText("No eligible candidate: nothing is ranked.")).toBeTruthy();
    expect(screen.getByText("no closed trades")).toBeTruthy();
    unmount();
    const rejected = candidate({ status: "not_eligible", status_reasons: ["max_drawdown cap 0.2 not met on the validation window"], rank: null });
    render(<ResultsView comparison={comparison("no_candidate_qualifies", [rejected])} />);
    expect(screen.getByText("no candidate qualifies")).toBeTruthy();
    expect(screen.getByText("not eligible (constraint failed on validation)")).toBeTruthy();
  });

  it("does not mark a sole leader as co-leading", () => {
    render(<ResultsView comparison={comparison("leading_candidate_identified", [candidate({ co_leading: true })])} />);
    expect(screen.queryByText(/co-leading/)).toBeNull();
    expect(screen.queryByText(/★/)).toBeNull();
  });

  it("marks co-leading ties", () => {
    const a = candidate({ co_leading: true });
    const b = candidate({ asset: "BBB", co_leading: true, rank: 2 });
    render(<ResultsView comparison={comparison("leading_candidate_identified", [a, b])} />);
    expect(screen.getByText(/2 co-leading candidates tie on the key/)).toBeTruthy();
  });
});

describe("FinalTestPanel", () => {
  it.each([
    ["frozen_criteria_met", /frozen criteria met/],
    ["frozen_criteria_not_met", /frozen criteria not met/],
    ["insufficient_evidence", /insufficient evidence/],
  ] as const)("renders the %s outcome with the benchmark comparison kept separate", (outcome, label) => {
    const block: FinalTestBlock = {
      state: "evaluated",
      outcome,
      outcome_reasons: outcome === "frozen_criteria_not_met" ? ["constraint value not met on the test window"] : [],
      thin_evidence: outcome === "frozen_criteria_met",
      acceptance: { constraint_value: 0.2, objective_minimum: 0.05 },
      benchmark_comparison: { return_vs_benchmark: "worse", drawdown_vs_benchmark: "better", excess_return_vs_benchmark: -0.03, note: "never a gate" },
      wording: "Beating buy-and-hold is not required.",
      reproducibility: null,
      candidate: { strategy_version_id: "v", asset: "AAA", run_id: null, metrics: METRICS, evidence: { grade: "thin", grade_reasons: [], descriptors: { closed_trades: 1, open_at_end: 0, measured_sessions: 1, median_holding_sessions: 1, holding_ratio: 1, clusters: 1, cluster_span_sessions: 5, concentration: { best_trade_share: null, best_three_share: null, best_month_share: null } }, limitations: [] } },
      benchmark: { run_id: null, metrics: METRICS },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { mutations_enabled: true, items: [] })));
    render(
      <FinalTestPanel
        revisionId="rev-1"
        comparison={comparison("leading_candidate_identified", [candidate({})])}
        freeze={{ freeze_id: "f", candidate: { strategy_version_id: "11111111-aaaa", asset: "AAA" }, acceptance: { constraint_value: 0.2, objective_minimum: 0.05 }, frozen_at: "2026-10-07T20:00:00Z", co_leading_choice_reason: null }}
        finalTest={block}
        exposures={{ proposed_window: { start: "2019-04-01", end: "2019-12-31" }, assets: ["AAA"], count: 1, by_state: { run_recorded: 0, results_inspected: 1 }, items: [{ exposure_id: "e", asset: "AAA", range: { start: "2019-04-01", end: "2019-12-31" }, study_id: "s", revision_id: "rev-1", strategy_version_id: "v", reason: "final_test", is_rerun: false, state: "results_inspected", run_recorded_at: null, results_inspected_at: null, context: { study_name: "Sibling", revision_no: 1, strategy_name: "Fast", version_no: 1, outcome: "frozen_criteria_met", same_revision: false }, link: "/x" }], limitation: "The application records only final tests run inside it." }}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.getByText(/Benchmark comparison \(shown separately; not part of the outcome\)/)).toBeTruthy();
    expect(screen.getByText(/Return worse than buy-and-hold, drawdown better/)).toBeTruthy();
    expect(screen.getByText(/The initial ranking is never changed by the final test/)).toBeTruthy();
    expect(screen.getByText(/1 results inspected/)).toBeTruthy();
    expect(screen.getByText(/records only final tests run inside it/)).toBeTruthy();
    if (outcome === "frozen_criteria_met") {
      expect(screen.getByText("thin evidence")).toBeTruthy();
    }
  });

  it("shows a running final test instead of offering the action again", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { mutations_enabled: true, items: [] })));
    render(
      <FinalTestPanel
        revisionId="rev-1"
        comparison={comparison("leading_candidate_identified", [candidate({})])}
        freeze={{ freeze_id: "f", candidate: { strategy_version_id: "11111111-aaaa", asset: "AAA" }, acceptance: { constraint_value: 0.2, objective_minimum: 0.05 }, frozen_at: "2026-10-07T20:00:00Z", co_leading_choice_reason: null }}
        finalTest={{ state: "not_run" }}
        finalTestInFlight
        exposures={null}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText(/Final test running/)).toBeTruthy();
    expect(screen.queryByText(/Run the final test/)).toBeNull();
  });

  it("requires candidate and both acceptance values before freezing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { mutations_enabled: true, items: [] })));
    render(<FinalTestPanel revisionId="rev-1" comparison={comparison("leading_candidate_identified", [candidate({})])} freeze={null} finalTest={{ state: "not_frozen" }} exposures={null} onChanged={() => {}} />);
    const button = (await screen.findByText("Freeze candidate and acceptance criteria")) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Draft editor keeps the typed text on a failed request
// ---------------------------------------------------------------------------

describe("DraftEditor", () => {
  it("keeps the typed YAML and shows the typed error when saving fails", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && String(url).endsWith("/validate")) {
        return Promise.resolve(jsonResponse(200, { validation: { valid: false, errors: [{ code: "missing_field", path: "entry", message: "Field required" }], derived: null, explanation: null } }));
      }
      if (init?.method === "POST") {
        return Promise.resolve(jsonResponse(422, { detail: { code: "invalid_draft_input", field: "title", reason: "must be 1..120 characters" } }));
      }
      return Promise.resolve(jsonResponse(200, { mutations_enabled: true, items: [] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DraftEditor draftId={null} onNavigate={() => {}} />);
    const textarea = screen.getByLabelText("Specification (YAML)") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "spec_version: 1\nname: broken\n" } });
    const button = await screen.findByText("Create draft");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("Invalid input: title must be 1..120 characters");
    expect(screen.getByRole("alert").textContent).toContain("Your text is kept");
    expect((screen.getByLabelText("Specification (YAML)") as HTMLTextAreaElement).value).toBe("spec_version: 1\nname: broken\n");
    await waitFor(() => expect(screen.getByText("missing_field")).toBeTruthy());
  });
});

// ---------------------------------------------------------------------------
// Mode gate and wizard mapping
// ---------------------------------------------------------------------------

describe("ResearchGate", () => {
  it("explains a trading-mode API instead of rendering research content", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok", service: "x", version: "1", timestamp: "t", mode: "trading" })));
    render(
      <ResearchGate title="Research">
        <p>research content</p>
      </ResearchGate>,
    );
    await screen.findByText("This API runs in trading mode.");
    expect(screen.queryByText("research content")).toBeNull();
  });
});

describe("KillSwitchBannerForMode", () => {
  it("renders no kill-switch banner against a research-mode API", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).endsWith("/health")) {
        return Promise.resolve(jsonResponse(200, { status: "ok", service: "x", version: "1", timestamp: "t", mode: "research" }));
      }
      return Promise.resolve(jsonResponse(404, { detail: "Not Found" }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<KillSwitchBannerForMode />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(/Kill-switch state UNKNOWN/)).toBeNull();
  });

  it("keeps the banner against a trading-mode API", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).endsWith("/health")) {
        return Promise.resolve(jsonResponse(200, { status: "ok", service: "x", version: "1", timestamp: "t", mode: "trading" }));
      }
      return Promise.resolve(jsonResponse(200, { name: "kill_switch", state: "armed", is_tripped: false, last_changed_at: "t", last_change_actor: null, last_change_reason: null, last_change_run_id: null }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<KillSwitchBannerForMode />);
    await screen.findByText("Kill switch: ARMED");
  });
});

describe("wizard defaults and version selection", () => {
  it("starts with return_first selected and the risk cap, costs and name empty", () => {
    expect(EMPTY_WIZARD.objective).toBe("return_first");
    expect(EMPTY_WIZARD.constraintValue).toBe("");
    expect(EMPTY_WIZARD.slippageBps).toBe("");
    expect(EMPTY_WIZARD.commission).toBe("");
    expect(EMPTY_WIZARD.name).toBe("");
    expect(EMPTY_WIZARD.versionIds).toEqual([]);
    expect(EMPTY_WIZARD.kind).toBe("substantive");
  });

  it("sends every selected version once, older versions included, in selection order", () => {
    const settings = wizardToSettings({ ...EMPTY_WIZARD, versionIds: ["v2-old", "v3-latest", "v2-old", "other-family-v1"] });
    expect(settings.strategy_version_ids).toEqual(["v2-old", "v3-latest", "other-family-v1"]);
  });

  it("groups approved versions by family with the latest first and keeps older ones selectable", () => {
    const version = (id: string, family: string, no: number, name: string): import("@/lib/research/types").StrategyVersion => ({
      version_id: id,
      strategy_id: family,
      version_no: no,
      name,
      spec_sha256: "f".repeat(64),
      history_required: 10,
      history_minimum: 10,
      scale_class: "price_scale_free",
      source: "manual",
      parent_version_id: null,
      behaviour_differs_from_original: null,
      approved_at: "2026-10-07T00:00:00Z",
    });
    const families = groupVersions([version("b1", "fam-b", 1, "Beta"), version("a1", "fam-a", 1, "Alpha"), version("a2", "fam-a", 2, "Alpha renamed")]);
    expect(families.map((f) => f.name)).toEqual(["Alpha renamed", "Beta"]);
    expect(families[0].versions.map((v) => v.version_id)).toEqual(["a2", "a1"]);
  });

  it("pre-fills a new revision from the stored settings without inventing values", () => {
    const stored = comparison("leading_candidate_identified", []).settings;
    const state = settingsToWizard({ ...stored, costs: null, constraint_value: null, strategy_version_ids: ["old", "new"] }, "Study");
    expect(state.versionIds).toEqual(["old", "new"]);
    expect(state.slippageBps).toBe("");
    expect(state.constraintValue).toBe("");
    expect(state.objective).toBe("return_first");
    expect(state.assetsText).toBe("AAA");
    expect(wizardToSettings(state).strategy_version_ids).toEqual(["old", "new"]);
  });
});

describe("wizardToSettings", () => {
  it("never pre-fills costs, objective or constraint", () => {
    const settings = wizardToSettings({ ...EMPTY_WIZARD, objective: "", assetsText: "aapl msft, aapl" });
    expect(settings.costs).toBeUndefined();
    expect(settings.objective).toBeUndefined();
    expect(settings.constraint_value).toBeUndefined();
    expect(settings.assets).toEqual(["AAPL", "MSFT", "AAPL"]);
    const full = wizardToSettings({ ...EMPTY_WIZARD, slippageBps: "5", commission: "0", constraintValue: "0.2", assetListId: "list-1" });
    expect(full.costs).toEqual({ slippage_bps: "5", commission_per_order: "0" });
    expect(full.objective).toBe("return_first");
    expect(full.asset_list_id).toBe("list-1");
  });
});
