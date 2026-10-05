// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { PaperReconciliationPanel } from "./PaperReconciliationPanel";
import type { Reconciliation } from "./types";

const NOT_BLOCKING: Reconciliation = {
  scope: "account",
  run_id: "run-1",
  status: "succeeded",
  as_of_session: "2026-01-05",
  finding_count: 0,
  blocking_count: 0,
  blocks_execution: false,
  completed_at: "2026-01-05T21:00:00Z",
};

// The closed TradingBlocker values and their operator labels.
const BLOCKERS: Array<[string, string]> = [
  ["no_active_paper_strategy", "no active paper strategy"],
  ["strategy_disabled", "strategy disabled"],
  ["kill_switch_tripped", "kill switch tripped"],
  ["outcome_unresolved", "uncertain order outcome unresolved"],
  ["reconciliation_blocking", "reconciliation blocking"],
  ["unrecognized_broker_activity", "unrecognized broker activity"],
  ["working_order_commitments_unaccounted", "working order commitments unaccounted"],
];

afterEach(cleanup);

describe("PaperReconciliationPanel", () => {
  it.each(BLOCKERS)(
    "reconciliation panel never says does not block execution while trading is blocked (%s)",
    (reason, label) => {
      const { container } = render(
        <PaperReconciliationPanel
          reconciliation={NOT_BLOCKING}
          findings={[]}
          tradingBlockedReasons={[reason]}
        />,
      );

      expect(screen.getByText(`TRADING BLOCKED: ${label}`)).toBeTruthy();
      expect(container.textContent).not.toContain("does not block execution");
    },
  );

  it("shows every blocker when several apply", () => {
    render(
      <PaperReconciliationPanel
        reconciliation={NOT_BLOCKING}
        findings={[]}
        tradingBlockedReasons={["strategy_disabled", "kill_switch_tripped"]}
      />,
    );

    expect(screen.getByText("TRADING BLOCKED: strategy disabled")).toBeTruthy();
    expect(screen.getByText("TRADING BLOCKED: kill switch tripped")).toBeTruthy();
  });

  it("reconciliation panel never says does not block execution while the blockers are unknown or loading", () => {
    // `null` is both the loading and the failed-fetch state of useActivePaperStrategy.
    const { container } = render(
      <PaperReconciliationPanel
        reconciliation={NOT_BLOCKING}
        findings={[]}
        tradingBlockedReasons={null}
      />,
    );

    expect(screen.getByText("Trading permission unknown")).toBeTruthy();
    expect(container.textContent).not.toContain("does not block execution");
  });

  it("says does not block execution only for known-empty blockers and a non-blocking run", () => {
    render(
      <PaperReconciliationPanel
        reconciliation={NOT_BLOCKING}
        findings={[]}
        tradingBlockedReasons={[]}
      />,
    );

    expect(screen.getByText("does not block execution")).toBeTruthy();
    expect(screen.queryByText("Trading permission unknown")).toBeNull();
  });

  it("a blocking run keeps BLOCKS EXECUTION whatever the blockers are", () => {
    for (const reasons of [null, [], ["kill_switch_tripped"]]) {
      const { container } = render(
        <PaperReconciliationPanel
          reconciliation={{ ...NOT_BLOCKING, blocks_execution: true }}
          findings={[]}
          tradingBlockedReasons={reasons}
        />,
      );
      expect(screen.getByText("BLOCKS EXECUTION")).toBeTruthy();
      expect(container.textContent).not.toContain("does not block execution");
      cleanup();
    }
  });

  it("renders the Scope and completion time of either scope", () => {
    render(
      <PaperReconciliationPanel
        reconciliation={{ ...NOT_BLOCKING, scope: "strategy" }}
        findings={[]}
        tradingBlockedReasons={[]}
      />,
    );
    expect(screen.getByText("strategy")).toBeTruthy();
    expect(screen.getByText("2026-01-05T21:00:00Z")).toBeTruthy();
    cleanup();

    render(
      <PaperReconciliationPanel
        reconciliation={NOT_BLOCKING}
        findings={[]}
        tradingBlockedReasons={[]}
      />,
    );
    expect(screen.getByText("account")).toBeTruthy();
  });

  it("the null-reconciliation state keeps its text and adds the blockers only when known and non-empty", () => {
    const { container } = render(
      <PaperReconciliationPanel
        reconciliation={null}
        findings={[]}
        tradingBlockedReasons={["kill_switch_tripped"]}
      />,
    );
    expect(screen.getByText("No reconciliation has been recorded yet.")).toBeTruthy();
    expect(screen.getByText("Trading blocked: kill switch tripped")).toBeTruthy();
    cleanup();

    for (const reasons of [null, []]) {
      const view = render(
        <PaperReconciliationPanel
          reconciliation={null}
          findings={[]}
          tradingBlockedReasons={reasons}
        />,
      );
      expect(screen.getByText("No reconciliation has been recorded yet.")).toBeTruthy();
      expect(view.container.textContent).not.toContain("Trading blocked");
      cleanup();
    }
    expect(container).toBeTruthy();
  });
});
