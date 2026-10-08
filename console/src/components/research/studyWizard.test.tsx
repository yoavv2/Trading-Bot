// @vitest-environment jsdom
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AssetPicker } from "./AssetPicker";
import { ResearchPeriod, emptyWindows, type PeriodValue } from "./ResearchPeriod";
import { StudyAssistant } from "./StudyAssistant";
import { EMPTY_WIZARD, StudyWizard, settingsToWizard, wizardBlockers, wizardToSettings, wizardWindows } from "./StudyWizard";
import { planApplication } from "@/lib/research/proposal";
import { DEFAULT_SPLIT, computeWindows, todayIso, validatePeriod, yearsBefore } from "@/lib/research/windows";
import { mergeAssets } from "@/lib/research/assets";
import type { AssetList, StudyProposal, StudySettings } from "@/lib/research/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const LIST: AssetList = { list_id: "11111111-1111-1111-1111-111111111111", name: "Mega caps", tickers: ["AAPL", "NVDA"], updated_at: null };

function catalogFetch(items: { ticker: string; name: string | null }[]) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/catalog/search")) {
      const q = new URL(url, "http://x").searchParams.get("q") ?? "";
      const matched = items.filter((i) => i.ticker.startsWith(q.toUpperCase()) || (i.name ?? "").toLowerCase().includes(q.toLowerCase()));
      return jsonResponse(200, {
        as_of: "now",
        query: q,
        count: matched.length,
        items: matched.map((i) => ({ ...i, name_source: null, exchange: "NASDAQ", asset_type: "Stock", currency: "USD", catalog_start: "2010-01-04", catalog_end: "2026-10-07" })),
        name_coverage: { rows_named: 100, rows_total: 200 },
        name_search_note: "names partial",
      });
    }
    return jsonResponse(404, { detail: { code: "not_found" } });
  });
}

function Picker({ initialManual = [] as string[], initialList = "", onChange }: { initialManual?: string[]; initialList?: string; onChange?: (manual: string[], list: string) => void }) {
  // A tiny stateful host so the picker's callbacks round-trip like in the wizard.
  const [manual, setManual] = useState<string[]>(initialManual);
  const [list, setList] = useState(initialList);
  useEffect(() => {
    onChange?.(manual, list);
  }, [manual, list, onChange]);
  return (
    <AssetPicker
      manual={manual}
      onManualChange={setManual}
      listId={list}
      onListChange={setList}
      lists={[LIST]}
      listsFailure={null}
      maxAssets={25}
    />
  );
}

describe("AssetPicker", () => {
  it("searches the catalog, selects with the keyboard, shows removable tags and prevents duplicates", async () => {
    const fetchMock = catalogFetch([
      { ticker: "AAPL", name: "Apple Inc." },
      { ticker: "AAPB", name: null },
    ]);
    vi.stubGlobal("fetch", fetchMock);
    render(<Picker />);
    const input = screen.getByRole("combobox", { name: /Add assets/ });
    fireEvent.change(input, { target: { value: "aap" } });
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    await waitFor(() => expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(2));
    expect(screen.getByText("Apple Inc.")).toBeTruthy();
    expect(screen.getByText("(no name on record)")).toBeTruthy();
    expect(fetchMock.mock.calls[0][0]).toContain("/backend/api/v1/research/catalog/search?q=aap&limit=20");
    // ArrowDown moves the highlight to AAPB; Enter adds it.
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toContain("option-1");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(within(screen.getByRole("list", { name: "Selected assets" })).getByText("AAPB")).toBeTruthy();
    expect((input as HTMLInputElement).value).toBe("");
    // Adding it again is a no-op with a hint.
    fireEvent.change(input, { target: { value: "aapb" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("status").textContent).toContain("Already selected: AAPB");
    expect(within(screen.getByRole("list", { name: "Selected assets" })).getAllByRole("listitem")).toHaveLength(1);
    // Remove via the tag button.
    fireEvent.click(screen.getByRole("button", { name: "Remove AAPB" }));
    expect(screen.getByText("No assets yet.")).toBeTruthy();
  });

  it("adds every ticker of a bulk paste, normalised and de-duplicated", () => {
    vi.stubGlobal("fetch", catalogFetch([]));
    const seen: string[][] = [];
    render(<Picker onChange={(manual) => seen.push(manual)} />);
    const input = screen.getByRole("combobox", { name: /Add assets/ });
    fireEvent.paste(input, { clipboardData: { getData: () => "aapl, msft brk.b\nAAPL" } });
    expect(seen.at(-1)).toEqual(["AAPL", "MSFT", "BRK-B"]);
    expect(screen.getByRole("status").textContent).toContain("Added 3 tickers.");
    // Backspace on an empty field removes the last tag.
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(seen.at(-1)).toEqual(["AAPL", "MSFT"]);
  });

  it("shows loading, error and empty states", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(503, { detail: "down" })),
    );
    render(<Picker />);
    const input = screen.getByRole("combobox", { name: /Add assets/ });
    fireEvent.change(input, { target: { value: "zzz" } });
    expect(screen.getByText("Searching…")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Catalog search failed (503)"));
    cleanup();
    vi.stubGlobal("fetch", catalogFetch([]));
    render(<Picker />);
    fireEvent.change(screen.getByRole("combobox", { name: /Add assets/ }), { target: { value: "zzz" } });
    await waitFor(() => expect(screen.getByText(/No catalog match for "zzz"/).textContent).toContain("100 of 200 rows have a name"));
    // Enter still adds the typed ticker (readiness verifies it later).
    fireEvent.keyDown(screen.getByRole("combobox", { name: /Add assets/ }), { key: "Enter" });
    expect(within(screen.getByRole("list", { name: "Selected assets" })).getByText("ZZZ")).toBeTruthy();
  });

  it("shows a saved list's tickers as list tags equal to the stored snapshot, and detaches without editing the list", () => {
    vi.stubGlobal("fetch", catalogFetch([]));
    let latest: { manual: string[]; list: string } = { manual: [], list: "" };
    render(<Picker initialManual={["MSFT", "AAPL"]} initialList={LIST.list_id} onChange={(manual, list) => (latest = { manual, list })} />);
    const tags = within(screen.getByRole("list", { name: "Selected assets" })).getAllByRole("listitem");
    // Manual first (as typed order), then the list's extra tickers: exactly `mergeAssets`.
    expect(tags.map((t) => t.textContent?.replace(/×|list/g, "").trim())).toEqual(mergeAssets(["MSFT", "AAPL"], LIST.tickers));
    expect(tags[2].textContent).toContain("list");
    expect(screen.queryByRole("button", { name: "Remove NVDA" })).toBeNull();
    expect(screen.getByText(/stored exactly as shown/).textContent).toContain("3 asset(s) of up to 25");
    // Adding a list ticker by hand is reported, not duplicated.
    const input = screen.getByRole("combobox", { name: /Add assets/ });
    fireEvent.change(input, { target: { value: "nvda" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("status").textContent).toContain("Already included through the saved list: NVDA");
    // Detaching copies the snapshot into manual tags and clears the list reference; no PUT is issued.
    fireEvent.click(screen.getByRole("button", { name: /detach the list/ }));
    expect(latest).toEqual({ manual: ["MSFT", "AAPL", "NVDA"], list: "" });
    expect(screen.getByRole("button", { name: "Remove NVDA" })).toBeTruthy();
    expect((vi.mocked(fetch).mock.calls as unknown[][]).filter((call) => (call[1] as RequestInit | undefined)?.method === "PUT")).toHaveLength(0);
  });
});

describe("ResearchPeriod", () => {
  function Host({ initial, onValue }: { initial: PeriodValue; onValue: (v: PeriodValue) => void }) {
    const [value, setValue] = useState(initial);
    const issues = validatePeriod(value.rangeStart, value.rangeEnd, value.windows);
    return (
      <ResearchPeriod
        value={value}
        onChange={(next) => {
          setValue(next);
          onValue(next);
        }}
        issues={issues}
        revisionNote={null}
      />
    );
  }

  it("computes the windows from the range and the editable split, shows the timeline and switches to custom editing", () => {
    let latest: PeriodValue | null = null;
    render(<Host initial={{ rangeStart: "", rangeEnd: "", split: { ...DEFAULT_SPLIT }, mode: "preset", windows: emptyWindows() }} onValue={(v) => (latest = v)} />);
    expect(screen.getByText("Start date is required.")).toBeTruthy();
    expect(screen.queryByTestId("custom-periods")).toBeNull();
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2018-01-01" } });
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2025-12-31" } });
    const expected = computeWindows("2018-01-01", "2025-12-31", DEFAULT_SPLIT)!;
    expect(latest!.windows).toEqual(expected);
    expect(screen.getByTestId("window-development").textContent).toBe(`${expected.development.start} → ${expected.development.end}`);
    expect(screen.getByTestId("window-final_test").textContent).toBe(`${expected.final_test.start} → ${expected.final_test.end}`);
    expect(screen.getByText("Develop and refine the strategy.")).toBeTruthy();
    // Editing the split recomputes.
    fireEvent.change(screen.getByLabelText("Validation share percent"), { target: { value: "30" } });
    expect(screen.getByRole("alert").textContent).toContain("add up to 110%");
    fireEvent.change(screen.getByLabelText("Final test share percent"), { target: { value: "10" } });
    expect(latest!.windows).toEqual(computeWindows("2018-01-01", "2025-12-31", { development_pct: 60, validation_pct: 30, final_test_pct: 10 }));
    // Customize exposes the six dates pre-filled with the computed windows; an overlap is flagged beside the control.
    fireEvent.click(screen.getByRole("button", { name: "Customize periods" }));
    expect(screen.getByTestId("custom-periods")).toBeTruthy();
    expect(latest!.mode).toBe("custom");
    fireEvent.change(screen.getByLabelText("Validation start"), { target: { value: latest!.windows.development.end } });
    const validationStart = screen.getByLabelText("Validation start");
    expect(validationStart.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Validation must start after development ends (no overlap, no shared day).")).toBeTruthy();
    // Back to the preset recomputes from the current split.
    fireEvent.click(screen.getByRole("button", { name: "Use the preset split instead" }));
    expect(latest!.mode).toBe("preset");
    expect(validatePeriod(latest!.rangeStart, latest!.rangeEnd, latest!.windows)).toEqual([]);
  });

  it("offers period shortcuts and an invalid range message beside the end date", () => {
    let latest: PeriodValue | null = null;
    render(<Host initial={{ rangeStart: "2020-01-01", rangeEnd: "2019-01-01", split: { ...DEFAULT_SPLIT }, mode: "preset", windows: emptyWindows() }} onValue={(v) => (latest = v)} />);
    expect(screen.getByLabelText("End date").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("End date is before the start date.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Last 5 years" }));
    expect(latest!.rangeEnd).toBe(todayIso());
    expect(latest!.rangeStart).toBe(yearsBefore(todayIso(), 5));
    expect(latest!.windows.final_test.end).toBe(latest!.rangeEnd);
  });
});

const STORED: StudySettings = {
  mode: "single_asset_independent",
  strategy_version_ids: ["v-old", "v-new"],
  assets: ["MSFT", "AAPL", "NVDA"],
  asset_list_id: LIST.list_id,
  range: { start: "2016-01-04", end: "2019-12-31" },
  windows: { development: { start: "2017-01-03", end: "2018-06-29" }, validation: { start: "2018-07-02", end: "2019-03-29" }, final_test: { start: "2019-04-01", end: "2019-12-31" } },
  initial_capital: "100000",
  quantity_policy: "whole_shares",
  costs: { slippage_bps: "5", commission_per_order: "1" },
  objective: "risk_first",
  constraint_value: "0.05",
  provider: "tiingo",
  adjusted: true,
  note: "kept",
};

describe("revision preservation and submission agreement", () => {
  it("keeps every stored setting exactly, custom windows included, and resubmits them unchanged", () => {
    const state = settingsToWizard(STORED, "Study");
    expect(state.periodMode).toBe("custom");
    const settings = wizardToSettings(state);
    expect(settings.range).toEqual(STORED.range);
    expect(settings.windows).toEqual(STORED.windows);
    expect(settings.strategy_version_ids).toEqual(STORED.strategy_version_ids);
    expect(settings.assets).toEqual(STORED.assets);
    expect(settings.asset_list_id).toBe(STORED.asset_list_id);
    expect(settings.costs).toEqual(STORED.costs);
    expect(settings.objective).toBe("risk_first");
    expect(settings.constraint_value).toBe("0.05");
    expect(settings.quantity_policy).toBe("whole_shares");
    expect(settings.note).toBe("kept");
    expect(wizardBlockers(state, true, LIST.tickers)).toEqual([]);
  });

  it("opens in preset mode only when the default preset reproduces the stored windows to the day", () => {
    const preset = computeWindows("2018-01-01", "2025-12-31", DEFAULT_SPLIT)!;
    const state = settingsToWizard({ ...STORED, range: { start: "2018-01-01", end: "2025-12-31" }, windows: preset }, "Study");
    expect(state.periodMode).toBe("preset");
    expect(wizardWindows(state)).toEqual(preset);
    const shifted = settingsToWizard({ ...STORED, range: { start: "2018-01-01", end: "2025-12-31" }, windows: { ...preset, validation: { start: preset.validation.start, end: "2023-01-01" } } }, "Study");
    expect(shifted.periodMode).toBe("custom");
    expect(shifted.validation.end).toBe("2023-01-01");
  });

  it("the tags shown equal the assets the backend will snapshot (manual first, then the list)", () => {
    const state = { ...EMPTY_WIZARD, assets: ["msft", "aapl"], assetListId: LIST.list_id };
    const submitted = wizardToSettings(state);
    // The API stores dict.fromkeys([*assets, *list_tickers]) — mergeAssets reproduces it.
    expect([...(submitted.assets as string[]), ...LIST.tickers.filter((t) => !(submitted.assets as string[]).includes(t))]).toEqual(mergeAssets(state.assets, LIST.tickers));
    expect(mergeAssets(state.assets, LIST.tickers)).toEqual(["MSFT", "AAPL", "NVDA"]);
  });

  it("lists what blocks creation", () => {
    expect(wizardBlockers(EMPTY_WIZARD, false, [])).toEqual(["Study name is required.", "Select at least one approved strategy version.", "Add at least one asset (or choose a saved list).", "Fix the research period."]);
    expect(wizardBlockers({ ...EMPTY_WIZARD, versionIds: ["v"], assetListId: LIST.list_id, initialCapital: "0" }, true, LIST.tickers)).toEqual(["Fix the research period.", "Initial capital must be a positive number."]);
  });
});

// ---------------------------------------------------------------------------
// Study assistant
// ---------------------------------------------------------------------------

const ASSISTANT_OK = {
  enabled: true,
  configured: true,
  missing: [],
  provider: "anthropic",
  model: "claude-haiku-5-5",
  prompt_version: "s5-v2",
  limits: { max_requests_per_day: 10, max_output_tokens: 4096, max_input_characters: 20000, max_revisions_per_draft: 2, max_concurrent_requests: 1, timeout_seconds: 60, automatic_retries_on_invalid_output: 1 },
  usage: { requests_today: 1, remaining_today: 9, in_flight: 0, revisions_used: null },
  note: "Provider spend is yours.",
};

function proposal(overrides: Partial<StudyProposal["settings"]> = {}, extra: Partial<StudyProposal> = {}): StudyProposal {
  return {
    status: "ok",
    prompt_version: "study-v1",
    attempt_no: 1,
    summary: "Compare the approved trend-following versions on AAPL and MSFT over 2018–2025.",
    missing: [
      { field: "costs", detail: "slippage and commission were not given" },
      { field: "constraint_value", detail: "no max drawdown cap stated" },
    ],
    unsupported_requests: [],
    settings: {
      name: "Trend following AAPL/MSFT 2018-2025",
      assets: [
        { ticker: "AAPL", in_catalog: true, name: "Apple Inc." },
        { ticker: "MSFT", in_catalog: false, name: null },
      ],
      strategies: {
        resolved: [{ version_id: "v-trend-2", strategy_id: "fam-trend", name: "Trend following", version_no: 2, reference: "trend-following", matched_by: "name" }],
        unresolved: [{ reference: "mean reversion", reason: "no approved version matches" }],
      },
      range_start: "2018-01-01",
      range_end: "2025-12-31",
      split: null,
      windows: null,
      initial_capital: "",
      quantity_policy: "",
      slippage_bps: "",
      commission_per_order: "",
      objective: "",
      constraint_value: "",
      note: "",
      kind: "",
      ...overrides,
    },
    provenance: { provider: "anthropic", model: "claude-haiku-5-5", prompt_version: "study-v1", request_id: "req_1", stop_reason: "end_turn", input_tokens: 900, output_tokens: 200, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, deadline_seconds: 60, started_at: null, completed_at: null },
    ...extra,
  };
}

describe("planApplication", () => {
  it("patches only proposed fields, resolves strategies through backend ids and computes preset windows", () => {
    const plan = planApplication(proposal(), { ...EMPTY_WIZARD, assetListId: LIST.list_id, slippageBps: "5" }, false);
    expect(plan.patch.name).toBe("Trend following AAPL/MSFT 2018-2025");
    expect(plan.patch.versionIds).toEqual(["v-trend-2"]);
    expect(plan.patch.assets).toEqual(["AAPL", "MSFT"]);
    expect(plan.patch.assetListId).toBe("");
    expect(plan.patch.periodMode).toBe("preset");
    const expected = computeWindows("2018-01-01", "2025-12-31", DEFAULT_SPLIT)!;
    expect(plan.patch.development).toEqual(expected.development);
    expect(plan.patch.split).toEqual(DEFAULT_SPLIT);
    expect(plan.period).toEqual({ rangeStart: "2018-01-01", rangeEnd: "2025-12-31", mode: "preset", split: DEFAULT_SPLIT, windows: expected });
    expect(plan.patch.slippageBps).toBeUndefined();
    expect(plan.patch.constraintValue).toBeUndefined();
    expect(plan.changes.map((c) => c.label)).toEqual(["Study name", "Strategy versions", "Assets", "Saved list", "Start date", "End date", "Development", "Validation", "Final test"]);
    expect(plan.changes.find((c) => c.label === "Final test")).toEqual({ key: "finalTest", label: "Final test", from: "— → —", to: `${expected.final_test.start} → ${expected.final_test.end}` });
    expect(plan.changes.find((c) => c.label === "Assets")).toEqual({ key: "assets", label: "Assets", from: "—", to: "AAPL, MSFT" });
  });

  it("uses explicit windows as custom periods, keeps name and kind on a revision, never applies unresolved strategies", () => {
    const windows = { development: { start: "2018-01-01", end: "2021-12-31" }, validation: { start: "2022-01-01", end: "2023-12-31" }, final_test: { start: "2024-01-01", end: "2025-12-31" } };
    const plan = planApplication(proposal({ windows, strategies: { resolved: [], unresolved: [{ reference: "x", reason: "no approved version matches" }] }, kind: "smoke", slippage_bps: "5", commission_per_order: "1" }), EMPTY_WIZARD, true);
    expect(plan.patch.periodMode).toBe("custom");
    expect(plan.patch.finalTest).toEqual(windows.final_test);
    expect(plan.patch.versionIds).toBeUndefined();
    expect(plan.patch.name).toBeUndefined();
    expect(plan.patch.kind).toBeUndefined();
    expect(plan.patch.slippageBps).toBe("5");
    expect(plan.patch.commission).toBe("1");
  });
});

describe("StudyAssistant", () => {
  it("explains an unconfigured assistant and keeps the input disabled", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, { as_of: "now", assistant: { ...ASSISTANT_OK, enabled: false, configured: false, missing: ["TRADING_PLATFORM_RESEARCH__AI__ENABLED=true"] } })));
    render(<StudyAssistant current={EMPTY_WIZARD} isRevision={false} capability={{ state: "enabled", reason: null, catalog: null, loading: false }} onApply={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => expect(screen.getByText("The assistant is disabled.")).toBeTruthy());
    expect(screen.getByText("TRADING_PLATFORM_RESEARCH__AI__ENABLED=true")).toBeTruthy();
    expect((screen.getByPlaceholderText(/Compare the approved/) as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Propose settings" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("requests a proposal, shows what was understood, what is missing, resolved and unresolved strategies, then applies explicitly", async () => {
    const first = proposal();
    const revised = proposal({ assets: [{ ticker: "AAPL", in_catalog: true, name: "Apple Inc." }], range_end: "2024-12-31" }, { summary: "Revised: AAPL only through 2024." });
    const posts: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/research/assistant")) {
          return jsonResponse(200, { as_of: "now", assistant: ASSISTANT_OK });
        }
        if (url.endsWith("/api/v1/research/assistant/study-proposals") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          posts.push(body);
          return jsonResponse(200, { as_of: "now", proposal: body.base_proposal ? revised : first });
        }
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    const applied: { patch: Record<string, unknown>; summary: string }[] = [];
    render(<StudyAssistant current={EMPTY_WIZARD} isRevision={false} capability={{ state: "enabled", reason: null, catalog: null, loading: false }} onApply={(patch, summary) => applied.push({ patch, summary })} />);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Propose settings" }) as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "use the example" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "Propose settings" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Propose settings" }));
    await waitFor(() => expect(screen.getByTestId("study-proposal")).toBeTruthy());
    expect(posts[0]).toEqual({ user_text: "Compare the approved trend-following strategies on AAPL and MSFT from 2018 through 2025.", base_proposal: null });
    const card = within(screen.getByTestId("study-proposal"));
    expect(card.getByText(first.summary)).toBeTruthy();
    expect(card.getByText(/Missing \(2\)/)).toBeTruthy();
    expect(card.getByText(/no max drawdown cap stated/)).toBeTruthy();
    expect(card.getByText(/"mean reversion": no approved version matches/)).toBeTruthy();
    expect(card.getAllByText(/Trend following v2/).length).toBeGreaterThan(0);
    expect(card.getByText("not in catalog")).toBeTruthy();
    expect(card.getByTestId("window-timeline")).toBeTruthy();
    expect(card.getByText(/replaces \(8\)/)).toBeTruthy();
    expect(applied).toHaveLength(0);
    // Follow-up revision sends the previous proposal as the base.
    fireEvent.change(card.getByPlaceholderText(/use 2016 to 2024/), { target: { value: "AAPL only, through 2024" } });
    fireEvent.click(card.getByRole("button", { name: "Revise proposal" }));
    await waitFor(() => expect(screen.getByText("Revised: AAPL only through 2024.")).toBeTruthy());
    expect((posts[1] as { base_proposal: { settings: unknown } }).base_proposal.settings).toEqual(first.settings);
    expect(screen.getByText(/follow-up 1 on this page/)).toBeTruthy();
    // Apply is explicit and only patches the form.
    fireEvent.click(screen.getByRole("button", { name: "Apply to form" }));
    expect(applied).toHaveLength(1);
    expect(applied[0].patch.assets).toEqual(["AAPL"]);
    expect(applied[0].patch.rangeEnd).toBe("2024-12-31");
    expect(applied[0].summary).toContain("field(s) replaced");
    expect((vi.mocked(fetch).mock.calls as unknown[][]).filter((call) => String(call[0]).endsWith("/api/v1/research/studies"))).toHaveLength(0);
  });

  it("keeps the text and the last proposal when a request fails, with the typed reason", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/research/assistant")) {
          return jsonResponse(200, { as_of: "now", assistant: ASSISTANT_OK });
        }
        if (init?.method === "POST") {
          calls += 1;
          return calls === 1 ? jsonResponse(200, { as_of: "now", proposal: proposal() }) : jsonResponse(422, { detail: { code: "ai_output_invalid", findings: ["windows out of order"] } });
        }
        return jsonResponse(404, {});
      }),
    );
    render(<StudyAssistant current={EMPTY_WIZARD} isRevision={false} capability={{ state: "enabled", reason: null, catalog: null, loading: false }} onApply={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => screen.getByRole("button", { name: "Propose settings" }));
    fireEvent.change(screen.getByPlaceholderText(/Compare the approved/), { target: { value: "anything" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Propose settings" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Propose settings" }));
    await waitFor(() => screen.getByTestId("study-proposal"));
    fireEvent.change(screen.getByPlaceholderText(/use 2016 to 2024/), { target: { value: "make it weird" } });
    fireEvent.click(screen.getByRole("button", { name: "Revise proposal" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("did not match the study proposal contract"));
    expect(screen.getByRole("alert").textContent).toContain("windows out of order");
    expect(screen.getByTestId("study-proposal")).toBeTruthy();
    expect((screen.getByPlaceholderText(/use 2016 to 2024/) as HTMLTextAreaElement).value).toBe("make it weird");
  });
});

describe("planApplication regressions", () => {
  const CUSTOM: typeof EMPTY_WIZARD = {
    ...EMPTY_WIZARD,
    rangeStart: "2016-01-04",
    rangeEnd: "2019-12-31",
    split: { development_pct: 50, validation_pct: 25, final_test_pct: 25 },
    periodMode: "custom",
    development: { start: "2017-01-03", end: "2018-06-29" },
    validation: { start: "2018-07-02", end: "2019-03-29" },
    finalTest: { start: "2019-04-01", end: "2019-12-31" },
  };

  it("range-only proposal in custom mode: preview, split controls, timeline and submitted windows agree", () => {
    const plan = planApplication(proposal({ range_start: "2018-01-01", range_end: "2025-12-31" }), CUSTOM, false);
    // The split is patched with the windows (the form's own split, not a silent default).
    expect(plan.patch.split).toEqual(CUSTOM.split);
    expect(plan.patch.periodMode).toBe("preset");
    const expected = computeWindows("2018-01-01", "2025-12-31", CUSTOM.split)!;
    const applied = { ...CUSTOM, ...plan.patch };
    // Preview (plan.period) == what ResearchPeriod will render in preset mode == what is submitted.
    expect(plan.period.windows).toEqual(expected);
    expect(computeWindows(applied.rangeStart, applied.rangeEnd, applied.split)).toEqual(expected);
    expect(wizardToSettings(applied).windows).toEqual(expected);
    expect(validatePeriod(applied.rangeStart, applied.rangeEnd, wizardWindows(applied))).toEqual([]);
    // Every boundary that changes is listed, with its current and proposed dates.
    const labels = plan.changes.map((c) => c.label);
    expect(labels).toEqual(expect.arrayContaining(["Start date", "End date", "Periods", "Development", "Validation", "Final test"]));
    expect(plan.changes.find((c) => c.label === "Development")).toEqual({ key: "development", label: "Development", from: "2017-01-03 → 2018-06-29", to: `${expected.development.start} → ${expected.development.end}` });
    expect(plan.changes.find((c) => c.label === "Periods")!.to).toBe("preset split 50 / 25 / 25");
  });

  it("does not list a proposed value equal to the form's current value", () => {
    const plan = planApplication(proposal({ quantity_policy: "fractional", kind: "substantive", initial_capital: "100000", range_start: "", range_end: "" }), EMPTY_WIZARD, false);
    expect(plan.changes.map((c) => c.label)).toEqual(["Study name", "Strategy versions", "Assets"]);
    expect(plan.patch.kind).toBeUndefined();
    expect(plan.patch.quantityPolicy).toBeUndefined();
  });

  it("a proposal that only repeats the unchanged range keeps custom periods untouched", () => {
    const plan = planApplication(proposal({ range_start: "2016-01-04", range_end: "2019-12-31", slippage_bps: "5", commission_per_order: "1" }), CUSTOM, false);
    expect(plan.patch.periodMode).toBeUndefined();
    expect(plan.patch.split).toBeUndefined();
    expect(plan.patch.development).toBeUndefined();
    expect(plan.patch.rangeStart).toBeUndefined();
    expect(plan.period.mode).toBe("custom");
    expect(plan.period.windows).toEqual(wizardWindows(CUSTOM));
    expect(plan.changes.map((c) => c.label)).toEqual(expect.not.arrayContaining(["Start date", "End date", "Periods", "Development"]));
    expect(plan.patch.slippageBps).toBe("5");
  });

  it("a single range boundary recomputes from the form's other boundary, with one calculation for preview and patch", () => {
    const current = { ...EMPTY_WIZARD, rangeStart: "2018-01-01", rangeEnd: "2025-12-31", ...(() => { const w = computeWindows("2018-01-01", "2025-12-31", DEFAULT_SPLIT)!; return { development: w.development, validation: w.validation, finalTest: w.final_test }; })() };
    const plan = planApplication(proposal({ range_start: "", range_end: "2024-12-31" }), current, false);
    const expected = computeWindows("2018-01-01", "2024-12-31", DEFAULT_SPLIT)!;
    expect(plan.period).toEqual({ rangeStart: "2018-01-01", rangeEnd: "2024-12-31", mode: "preset", split: DEFAULT_SPLIT, windows: expected });
    expect(plan.patch.rangeStart).toBeUndefined();
    expect(plan.patch.rangeEnd).toBe("2024-12-31");
    expect(plan.patch.finalTest).toEqual(expected.final_test);
    expect(plan.changes.map((c) => c.label)).toEqual(["Study name", "Strategy versions", "Assets", "End date", "Development", "Validation", "Final test"]);
    expect(wizardToSettings({ ...current, ...plan.patch }).windows).toEqual(expected);
  });
});

describe("date shortcuts", () => {
  it("clamps leap days when going back whole years", () => {
    expect(yearsBefore("2024-02-29", 1)).toBe("2023-02-28");
    expect(yearsBefore("2024-02-29", 4)).toBe("2020-02-29");
    expect(yearsBefore("2025-03-31", 5)).toBe("2020-03-31");
    expect(validatePeriod(yearsBefore("2024-02-29", 1), "2024-02-29", computeWindows(yearsBefore("2024-02-29", 1), "2024-02-29", DEFAULT_SPLIT)!)).toEqual([]);
  });

  it("uses the local calendar date, not the UTC date, at a timezone boundary", () => {
    const previous = process.env.TZ;
    process.env.TZ = "Asia/Jerusalem"; // UTC+2/+3: local midnight is still the previous day in UTC
    try {
      const localJustAfterMidnight = new Date(2026, 2, 1, 0, 30); // 2026-03-01 00:30 local
      expect(localJustAfterMidnight.toISOString().slice(0, 10)).toBe("2026-02-28");
      expect(todayIso(localJustAfterMidnight)).toBe("2026-03-01");
    } finally {
      process.env.TZ = previous;
    }
  });
});

describe("StudyAssistant follow-up context", () => {
  it("sends outstanding unsupported requests back with the base proposal and keeps them visible after an assets-only change", async () => {
    const unsupported = [{ code: "short_selling", detail: "short positions cannot be expressed in a Study" }];
    const first = proposal({}, { unsupported_requests: unsupported });
    const posts: { base_proposal: { unsupported_requests?: unknown } | null }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/research/assistant")) {
          return jsonResponse(200, { as_of: "now", assistant: ASSISTANT_OK });
        }
        if (init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          posts.push(body);
          // The server answers a revision from the base it was given: the unsupported list travels with it.
          const revised = body.base_proposal
            ? proposal({ assets: [{ ticker: "NVDA", in_catalog: true, name: "NVIDIA" }] }, { summary: "Assets changed to NVDA.", unsupported_requests: body.base_proposal.unsupported_requests })
            : first;
          return jsonResponse(200, { as_of: "now", proposal: revised });
        }
        return jsonResponse(404, {});
      }),
    );
    render(<StudyAssistant current={EMPTY_WIZARD} isRevision={false} capability={{ state: "enabled", reason: null, catalog: null, loading: false }} onApply={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => screen.getByRole("button", { name: "Propose settings" }));
    fireEvent.change(screen.getByPlaceholderText(/Compare the approved/), { target: { value: "long only, no shorts please" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Propose settings" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Propose settings" }));
    await waitFor(() => screen.getByTestId("study-proposal"));
    expect(screen.getByText("short_selling")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/use 2016 to 2024/), { target: { value: "NVDA only" } });
    fireEvent.click(screen.getByRole("button", { name: "Revise proposal" }));
    await waitFor(() => screen.getByText("Assets changed to NVDA."));
    expect(posts[1].base_proposal?.unsupported_requests).toEqual(unsupported);
    expect(screen.getByText("short_selling")).toBeTruthy();
    expect(screen.getByText(/kept through follow-ups/)).toBeTruthy();
    // The counter is labelled as this page's own, not a server limit; no cap disables the follow-up.
    expect(screen.getByText(/follow-up 1 on this page/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Revise proposal" }) as HTMLButtonElement).disabled).toBe(true); // only because the field is empty again
    fireEvent.change(screen.getByPlaceholderText(/use 2016 to 2024/), { target: { value: "again" } });
    expect((screen.getByRole("button", { name: "Revise proposal" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("StudyWizard", () => {
  it("renders the renamed period section, the picker and the assistant, and creates nothing until the button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/asset-lists")) return jsonResponse(200, { as_of: "now", count: 1, lists: [LIST] });
        if (url.endsWith("/research/catalog")) return jsonResponse(200, { as_of: "now", catalog: { rows_total: 1, rows_named: 1, name_search_note: "", coverage_note: "", last_synced_at: null, max_assets_per_study: 25 } });
        if (url.includes("/strategies/versions")) return jsonResponse(200, { as_of: "now", count: 0, versions: [] });
        if (url.includes("/job-types")) return jsonResponse(200, { as_of: "now", mutations_enabled: true, job_types: [] });
        if (url.endsWith("/api/v1/research/assistant")) return jsonResponse(200, { as_of: "now", assistant: ASSISTANT_OK });
        return jsonResponse(404, { detail: { code: "not_found" } });
      }),
    );
    render(<StudyWizard onNavigate={() => {}} />);
    expect(screen.getByText("3. Research period")).toBeTruthy();
    expect(screen.queryByText("3. Dates and windows")).toBeNull();
    expect(screen.getByRole("combobox", { name: /Add assets/ })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Study assistant" })).toBeTruthy();
    // Standard view: two range dates only (no eight inputs).
    expect(screen.getAllByLabelText(/date$/i)).toHaveLength(2);
    expect(screen.queryByTestId("custom-periods")).toBeNull();
    const create = screen.getByRole("button", { name: "Create study (revision 1)" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    expect(screen.getByRole("list", { name: "Before creating" }).textContent).toContain("Fix the research period.");
    await waitFor(() => expect(screen.getByRole("option", { name: "Mega caps (2)" })).toBeTruthy());
  });
});
