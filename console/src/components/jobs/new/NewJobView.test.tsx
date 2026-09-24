// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { NewJobView } from "./NewJobView";
import { StrategyOverviewPanel } from "@/components/strategy/StrategyOverviewPanel";
import type { JobTypesCatalog } from "@/components/jobs/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const CATALOG_ENABLED: JobTypesCatalog = {
  mutations_enabled: true,
  items: [
    {
      job_type: "backtest",
      description:
        "Run a historical backtest of a registered strategy over an explicit date range.",
      cancellation_mode: "step_boundary",
    },
  ],
};

const CATALOG_DISABLED: JobTypesCatalog = {
  mutations_enabled: false,
  items: [],
};

const STRATEGY_DETAIL = {
  strategy: {
    strategy_id: "trend_following_daily",
    display_name: "Trend Following Daily",
    version: "v1",
    enabled: true,
    description: "desc",
    config_reference: "config/ref.yaml",
    universe: ["AAPL"],
    universe_size: 1,
    indicators: {},
    risk: {},
    exits: {},
  },
  operator_reads: {},
};

const STRATEGIES_LIST = {
  count: 1,
  strategies: [
    { strategy_id: "trend_following_daily", display_name: "Trend Following Daily" },
  ],
};

/**
 * Routes the console's single global fetch() by URL: GET /api/v1/job-types
 * (NewJobView, and useMutationCapability inside StrategyOverviewPanel),
 * GET /api/v1/strategies/trend_following_daily (StrategyOverviewPanel), and
 * GET /api/v1/strategies (BacktestJobForm's own strategy select) are all
 * dispatched through this one stub.
 */
function makeFetchRouter(
  options: { catalog?: JobTypesCatalog; jobTypesStatus?: number } = {},
) {
  const catalog = options.catalog ?? CATALOG_ENABLED;
  const jobTypesStatus = options.jobTypesStatus ?? 200;
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/job-types")) {
      return Promise.resolve(
        jobTypesStatus === 200
          ? jsonResponse(200, catalog)
          : jsonResponse(jobTypesStatus, { detail: "boom" }),
      );
    }
    if (url.includes("/backend/api/v1/strategies/trend_following_daily")) {
      return Promise.resolve(jsonResponse(200, STRATEGY_DETAIL));
    }
    if (url.includes("/backend/api/v1/strategies")) {
      return Promise.resolve(jsonResponse(200, STRATEGIES_LIST));
    }
    throw new Error(`NewJobView.test.tsx: unexpected fetch URL ${url}`);
  });
  return { fn };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("NewJobView", () => {
  it("lists one link per catalog item with its description when no type is selected", async () => {
    const { fn } = makeFetchRouter();
    vi.stubGlobal("fetch", fn);

    render(<NewJobView jobType={null} initialParams={{}} onNavigate={vi.fn()} />);
    await flush();

    const link = screen.getByRole("link", { name: "backtest" });
    expect(link.getAttribute("href")).toBe("/jobs/new?type=backtest");
    expect(
      screen.getByText(
        "Run a historical backtest of a registered strategy over an explicit date range.",
      ),
    ).toBeTruthy();
  });

  it('shows the "no submission form" fallback and a link back to /jobs for an unmapped type', async () => {
    const { fn } = makeFetchRouter();
    vi.stubGlobal("fetch", fn);

    render(
      <NewJobView jobType="unknown_kind" initialParams={{}} onNavigate={vi.fn()} />,
    );
    await flush();

    expect(
      screen.getByText('No submission form is available for "unknown_kind" yet.'),
    ).toBeTruthy();
    const backLink = screen.getByRole("link", { name: "Back to Jobs" });
    expect(backLink.getAttribute("href")).toBe("/jobs");
  });

  it('renders the Submit Backtest form when jobType is "backtest"', async () => {
    const { fn } = makeFetchRouter();
    vi.stubGlobal("fetch", fn);

    render(
      <NewJobView jobType="backtest" initialParams={{}} onNavigate={vi.fn()} />,
    );
    await flush();

    expect(screen.getByRole("button", { name: "Submit Backtest" })).toBeTruthy();
  });

  it("D-21: a catalog fetch failure keeps a mapped type's form visible, disabled with the honest-unknown reason", async () => {
    const { fn } = makeFetchRouter({ jobTypesStatus: 500 });
    vi.stubGlobal("fetch", fn);

    render(
      <NewJobView jobType="backtest" initialParams={{}} onNavigate={vi.fn()} />,
    );
    await flush();

    const button = screen.getByRole("button", { name: "Submit Backtest" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Mutation availability unknown — GET /api/v1/job-types failed"),
    ).toBeTruthy();
  });
});

describe("StrategyOverviewPanel Run backtest shortcut (D-18)", () => {
  it("links to /jobs/new?type=backtest&strategy_id=trend_following_daily when mutations are enabled", async () => {
    const { fn } = makeFetchRouter({ catalog: CATALOG_ENABLED });
    vi.stubGlobal("fetch", fn);

    render(<StrategyOverviewPanel />);
    await flush();

    const link = screen.getByRole("link", { name: "Run backtest" });
    expect(link.getAttribute("href")).toBe(
      "/jobs/new?type=backtest&strategy_id=trend_following_daily",
    );
  });

  it("disables Run backtest with the D-21 reason when mutations are disabled", async () => {
    const { fn } = makeFetchRouter({ catalog: CATALOG_DISABLED });
    vi.stubGlobal("fetch", fn);

    render(<StrategyOverviewPanel />);
    await flush();

    const button = screen.getByRole("button", { name: "Run backtest" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });
});
