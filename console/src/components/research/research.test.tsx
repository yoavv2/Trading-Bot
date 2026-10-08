// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ValidationPanel } from "./ValidationPanel";
import { ReadinessPanel } from "./ReadinessPanel";
import { ResultsView } from "./ResultsView";
import { FinalTestPanel } from "./FinalTestPanel";
import { DraftEditor } from "./DraftEditor";
import { ResearchGate } from "./ResearchGate";
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
    expect(screen.getByRole("alert").textContent).toContain("make dev");
    expect(screen.queryByText("research content")).toBeNull();
  });

  it("names an unreachable API with the start command and a retry instead of loading forever", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ResearchGate title="Research">
        <p>research content</p>
      </ResearchGate>,
    );
    await screen.findByText("The research API is unreachable.");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("TRADING_CONSOLE_API_BASE_URL");
    expect(alert.textContent).toContain("make dev");
    expect(screen.queryByText("research content")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("reports a failing /health status instead of rendering research panels", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(502, { detail: "bad gateway" })));
    render(
      <ResearchGate title="Research">
        <p>research content</p>
      </ResearchGate>,
    );
    await screen.findByText("The research API answered 502 to GET /health.");
    expect(screen.queryByText("research content")).toBeNull();
  });

  it("renders the page content against a research-mode API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok", service: "x", version: "1", timestamp: "t", mode: "research" })));
    render(
      <ResearchGate title="Research">
        <p>research content</p>
      </ResearchGate>,
    );
    await screen.findByText("research content");
    expect(screen.queryByRole("alert")).toBeNull();
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

// ---------------------------------------------------------------------------
// S5 assistant panel
// ---------------------------------------------------------------------------

import { AssistantPanel } from "./AssistantPanel";

const ASSISTANT_OK = {
  enabled: true,
  configured: true,
  missing: [],
  provider: "anthropic",
  model: "claude-haiku-5-5",
  prompt_version: "s5-v2",
  limits: { max_requests_per_day: 20, max_output_tokens: 2000, max_input_characters: 20000, max_revisions_per_draft: 5, max_concurrent_requests: 1, timeout_seconds: 60, automatic_retries_on_invalid_output: 1 },
  usage: { requests_today: 2, remaining_today: 18, in_flight: 0, revisions_used: null },
  note: "Limits are enforced per attempt; provider spending controls are separate.",
};

const PROPOSAL = {
  ai_draft_id: "0a1b2c3d-0000-4000-8000-000000000001",
  kind: "draft",
  status: "ok",
  failure_code: null,
  attempt_no: 1,
  retry_of_ai_draft_id: null,
  parent_ai_draft_id: null,
  draft_id: null,
  request_token: "t",
  user_text: "trend",
  base_yaml_text: null,
  yaml_text: "spec_version: 1\nname: Trend following 50/200\n",
  note: "A moving-average trend filter.",
  unsupported_requests: [{ code: "stop_or_target_price_not_supported", detail: "a 5% stop was requested" }],
  validation: { valid: true, errors: [], derived: { name: "Trend following 50/200", spec_sha256: "abc", history_required: 200, history_minimum: 200, scale_class: "scale_free", terms_used: ["sma_fast"], operators_used: ["gt"] }, explanation: "Enter long when 1. close is above sma_slow." },
  explanation: "Enter long when 1. close is above sma_slow.",
  spec_sha256: "abc",
  provenance: { provider: "anthropic", model: "claude-haiku-5-5", prompt_version: "s5-v2", request_id: "req_1", stop_reason: "end_turn", input_tokens: 1200, output_tokens: 300, cache_read_input_tokens: 1000, cache_creation_input_tokens: 0, deadline_seconds: 60, started_at: "t", completed_at: "t" },
  applied_at: null,
  created_at: "t",
};

const CAPABILITY = { state: "enabled" as const, reason: null, catalog: null, loading: false };

describe("AssistantPanel", () => {
  it("explains a disabled assistant with the exact settings to set and keeps the input disabled", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { as_of: "t", assistant: { ...ASSISTANT_OK, enabled: false, configured: false, missing: ["TRADING_PLATFORM_RESEARCH__AI__ENABLED=true", "ANTHROPIC_API_KEY (or TRADING_PLATFORM_RESEARCH__AI__API_KEY)"] } })));
    render(<AssistantPanel draftId={null} editorYaml="" editorIsStarter capability={CAPABILITY} onUseInEditor={() => {}} onApplied={() => {}} />);
    await screen.findByText("The assistant is disabled.");
    expect(screen.getByText("ANTHROPIC_API_KEY (or TRADING_PLATFORM_RESEARCH__AI__API_KEY)")).toBeTruthy();
    expect((screen.getByLabelText("Describe the strategy") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByText("Draft with the assistant") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows an exhausted allowance and refuses to ask", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { as_of: "t", assistant: { ...ASSISTANT_OK, usage: { ...ASSISTANT_OK.usage, requests_today: 20, remaining_today: 0 } } })));
    render(<AssistantPanel draftId={null} editorYaml="" editorIsStarter capability={CAPABILITY} onUseInEditor={() => {}} onApplied={() => {}} />);
    await screen.findByText(/allowance is used up \(20\/20\)/);
    fireEvent.change(screen.getByLabelText("Describe the strategy"), { target: { value: "trend" } });
    expect((screen.getByText("Draft with the assistant") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders a proposal with YAML, unsupported requests, note, findings and explanation; one click sends one tokenised request", async () => {
    const posts: Array<Record<string, unknown>> = [];
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && String(url).endsWith("/assistant/proposals")) {
        posts.push(JSON.parse(String(init.body)));
        return new Promise((resolve) => setTimeout(() => resolve(jsonResponse(200, { as_of: "t", proposal: PROPOSAL })), 30));
      }
      return Promise.resolve(jsonResponse(200, { as_of: "t", assistant: ASSISTANT_OK }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const used: string[] = [];
    render(<AssistantPanel draftId={null} editorYaml="" editorIsStarter capability={CAPABILITY} onUseInEditor={(y) => used.push(y)} onApplied={() => {}} />);
    await screen.findByText(/today 2\/20 requests/);
    fireEvent.change(screen.getByLabelText("Describe the strategy"), { target: { value: "50/200 trend with a 5% stop" } });
    const button = screen.getByText("Draft with the assistant") as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByTestId("assistant-proposal");
    expect(posts).toHaveLength(1);
    expect(posts[0].user_text).toBe("50/200 trend with a 5% stop");
    expect(typeof posts[0].request_token).toBe("string");
    expect(posts[0].base_yaml_text).toBeUndefined();
    expect(screen.getByText("stop_or_target_price_not_supported")).toBeTruthy();
    expect(screen.getByText("a 5% stop was requested")).toBeTruthy();
    expect(screen.getByText("A moving-average trend filter.")).toBeTruthy();
    expect(screen.getByText("Enter long when 1. close is above sma_slow.")).toBeTruthy();
    expect((screen.getByLabelText("Proposed specification (YAML, editable)") as HTMLTextAreaElement).value).toContain("Trend following 50/200");
    fireEvent.click(screen.getByText("Use in editor"));
    expect(used).toEqual(["spec_version: 1\nname: Trend following 50/200\n"]);
    // editing the proposal blocks the verbatim apply until reset
    fireEvent.change(screen.getByLabelText("Proposed specification (YAML, editable)"), { target: { value: "spec_version: 1\nname: edited\n" } });
    expect((screen.getByText("Apply as a new draft") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("reset to the proposal"));
    expect((screen.getByText("Apply as a new draft") as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps the typed text and the last proposal when a request fails, and shows the typed reason", async () => {
    let calls = 0;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && String(url).endsWith("/assistant/proposals")) {
        calls += 1;
        if (calls === 1) {
          return Promise.resolve(jsonResponse(200, { as_of: "t", proposal: PROPOSAL }));
        }
        return Promise.resolve(jsonResponse(504, { detail: { code: "ai_timeout", deadline_seconds: 60, ai_draft_id: "x" } }));
      }
      return Promise.resolve(jsonResponse(200, { as_of: "t", assistant: ASSISTANT_OK }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AssistantPanel draftId="d1" editorYaml="spec_version: 1\nname: seed\n" editorIsStarter={false} capability={CAPABILITY} onUseInEditor={() => {}} onApplied={() => {}} />);
    await screen.findByText(/today 2\/20 requests/);
    const input = screen.getByLabelText("Describe the correction");
    fireEvent.change(input, { target: { value: "use 60 days" } });
    const button = screen.getByText("Request a revision") as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await screen.findByTestId("assistant-proposal");
    fireEvent.change(screen.getByLabelText("Describe the correction"), { target: { value: "and exit at 2% below" } });
    await waitFor(() => expect((screen.getByText("Request a revision") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText("Request a revision"));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("did not answer within the deadline (60s)");
    expect(screen.getByRole("alert").textContent).toContain("Your text and the last proposal are kept");
    expect((screen.getByLabelText("Describe the correction") as HTMLTextAreaElement).value).toBe("and exit at 2% below");
    expect(screen.getByTestId("assistant-proposal")).toBeTruthy();
  });

  it("applies a proposal to the current draft explicitly and reports an already-applied repeat", async () => {
    let applies = 0;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST" && String(url).endsWith("/apply")) {
        applies += 1;
        expect(JSON.parse(String(init.body))).toEqual({ draft_id: "d1" });
        return Promise.resolve(jsonResponse(200, { as_of: "t", draft: { draft_id: "d1", title: "T", yaml_text: PROPOSAL.yaml_text, source: "assistant", parent_version_id: null, created_at: null, updated_at: null }, proposal: { ...PROPOSAL, applied_at: "t" }, already_applied: applies > 1 }));
      }
      if (init?.method === "POST") {
        return Promise.resolve(jsonResponse(200, { as_of: "t", proposal: PROPOSAL }));
      }
      return Promise.resolve(jsonResponse(200, { as_of: "t", assistant: { ...ASSISTANT_OK, usage: { ...ASSISTANT_OK.usage, revisions_used: 1 } } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const applied: string[] = [];
    render(<AssistantPanel draftId="d1" editorYaml="spec_version: 1\nname: seed\n" editorIsStarter={false} capability={CAPABILITY} onUseInEditor={() => {}} onApplied={(d) => applied.push(d.draft_id)} />);
    await screen.findByText(/this draft 1\/5 revisions/);
    fireEvent.change(screen.getByLabelText("Describe the correction"), { target: { value: "x" } });
    await waitFor(() => expect((screen.getByText("Request a revision") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText("Request a revision"));
    await screen.findByTestId("assistant-proposal");
    fireEvent.click(screen.getByText("Apply as this draft's YAML"));
    await screen.findByText("Proposal applied to the draft.");
    expect(applied).toEqual(["d1"]);
    fireEvent.click(screen.getByText("Apply as this draft's YAML"));
    await screen.findByText("This proposal was already applied to the draft.");
  });
});
