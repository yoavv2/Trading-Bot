// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { RiskEvaluationJobForm } from "./RiskEvaluationJobForm";
import type { JobTypeCatalogItem } from "../types";
import type { MutationCapability } from "@/lib/useMutationCapability";

/**
 * WR-C-07: the four strategy-bearing forms seed strategy_id from an arbitrary
 * `?strategy_id=` deep-link. The select must never show "Select a strategy…"
 * while the form would submit a hidden id: a value that is not one of the
 * loaded options blocks submit, and the submitted id is the trimmed, verified
 * option value.
 */

type FormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

// 20.1-14: the reconciliation and broker-order-sync forms submit account scope and have no
// strategy field (their own tests pin the payload), so they are no longer rows here.
const FORMS: Array<{ name: string; Form: ComponentType<FormProps>; submit: string; jobType: string }> = [
  { name: "RiskEvaluationJobForm", Form: RiskEvaluationJobForm, submit: "Submit Risk Evaluation", jobType: "risk-evaluation" },
];

const CAPABILITY: MutationCapability = {
  state: "enabled",
  reason: null,
  catalog: null,
  loading: false,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(strategies: "ok" | "fail") {
  const posts: unknown[] = [];
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/strategies")) {
      return Promise.resolve(
        strategies === "ok"
          ? jsonResponse(200, {
              count: 1,
              strategies: [
                { strategy_id: "trend_following_daily", display_name: "Trend Following Daily" },
              ],
            })
          : jsonResponse(500, { detail: "boom" }),
      );
    }
    if (url.includes("/backend/api/v1/jobs")) {
      posts.push(JSON.parse(init?.body as string));
      return Promise.resolve(
        jsonResponse(202, {
          job_id: "job-1",
          job_type: "x",
          status: "queued",
          links: { self: "", progress: "", logs: "", events: "" },
        }),
      );
    }
    throw new Error(`strategySelection.test.tsx: unexpected fetch URL ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return posts;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function catalog(jobType: string): JobTypeCatalogItem {
  return {
    job_type: jobType,
    description: "d",
    cancellation_mode: "queued_only",
    submission_defaults: { as_of_session: "2026-01-02" },
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe.each(FORMS)("$name strategy selection (WR-C-07)", ({ Form, submit, jobType }) => {
  function renderForm(seed: string | undefined) {
    return render(
      <Form
        catalogEntry={catalog(jobType)}
        capability={CAPABILITY}
        initialParams={seed === undefined ? {} : { strategy_id: seed }}
        onNavigate={vi.fn()}
      />,
    );
  }
  const submitButton = () =>
    screen.getByRole("button", { name: submit }) as HTMLButtonElement;

  it("blocks submit for an unregistered deep-link id while the select shows the placeholder", async () => {
    const posts = stubFetch("ok");
    renderForm("not_a_strategy");
    await flush();

    const select = screen.getByLabelText("Strategy") as HTMLSelectElement;
    expect(select.selectedOptions[0].textContent).toBe("Select a strategy…");
    expect(submitButton().disabled).toBe(true);
    fireEvent.click(submitButton());
    await flush();
    expect(posts).toHaveLength(0);
  });

  it("trims a whitespace-padded deep-link id, selects it, and submits the trimmed id", async () => {
    const posts = stubFetch("ok");
    renderForm("  trend_following_daily  ");
    await flush();

    expect((screen.getByLabelText("Strategy") as HTMLSelectElement).value).toBe(
      "trend_following_daily",
    );
    expect(submitButton().disabled).toBe(false);
    fireEvent.click(submitButton());
    await flush();

    expect(posts).toHaveLength(1);
    expect((posts[0] as { payload: { strategy_id: string } }).payload.strategy_id).toBe(
      "trend_following_daily",
    );
  });

  it("blocks submit when the strategies list failed to load, even with a seeded id", async () => {
    const posts = stubFetch("fail");
    renderForm("trend_following_daily");
    await flush();

    expect(submitButton().disabled).toBe(true);
    fireEvent.click(submitButton());
    await flush();
    expect(posts).toHaveLength(0);
  });

  it("keeps a valid seed submittable once the list has loaded (deep link preserved while loading)", async () => {
    stubFetch("ok");
    renderForm("trend_following_daily");
    // Before the list resolves the seed is retained but not yet verified.
    expect(submitButton().disabled).toBe(true);
    await flush();
    expect(submitButton().disabled).toBe(false);
  });
});
