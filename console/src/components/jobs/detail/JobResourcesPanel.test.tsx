// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { JobResourcesPanel } from "./JobResourcesPanel";
import type { JobResource } from "../types";

afterEach(() => {
  cleanup();
});

describe("JobResourcesPanel — empty-state copy", () => {
  it("shows the honest non-terminal empty copy for a running Job", () => {
    render(<JobResourcesPanel resources={[]} status="running" />);
    expect(
      screen.getByText(
        "No linked resources yet. This panel updates automatically if the Job's run creates one.",
      ),
    ).toBeTruthy();
  });

  it("keeps the unchanged terminal empty copy for a succeeded Job", () => {
    render(<JobResourcesPanel resources={[]} status="succeeded" />);
    expect(
      screen.getByText("This Job produced no linked resources."),
    ).toBeTruthy();
  });
});

describe("JobResourcesPanel — market_data_ingestion_run (D-07)", () => {
  it("renders as plain '{kind}: {id}' text with no link", () => {
    const resources: JobResource[] = [
      {
        kind: "market_data_ingestion_run",
        id: "ingest-run-1",
        status: "succeeded",
        links: {},
      },
    ];
    render(<JobResourcesPanel resources={resources} status="succeeded" />);

    expect(
      screen.getByText("market_data_ingestion_run: ingest-run-1"),
    ).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: "market_data_ingestion_run: ingest-run-1" }),
    ).toBeNull();
  });
});

describe("JobResourcesPanel — strategy_run linking (unchanged, Phase 19)", () => {
  it("links a strategy_run resource to /runs/<id>", () => {
    const resources: JobResource[] = [
      { kind: "strategy_run", id: "run-1", status: "succeeded", links: { self: "/x" } },
    ];
    render(<JobResourcesPanel resources={resources} status="succeeded" />);

    const link = screen.getByRole("link", { name: "strategy_run: run-1" });
    expect(link.getAttribute("href")).toBe("/runs/run-1");
  });
});
