// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  StrictMode,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentProps,
} from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ControlConfirmDialog } from "./ControlConfirmDialog";
import {
  KILL_SWITCH_CHANGED_EVENT,
  STRATEGY_CHANGED_EVENT,
  dispatchControlChanged,
  useControlChanged,
} from "./controlEvents";
import { StrategyStatusBadge } from "../strategy/StrategyStatusBadge";
import type { MutationResult } from "@/lib/api";

type ChangedResult = MutationResult<{ changed: boolean }>;

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
});

describe("ControlConfirmDialog", () => {
  it("shows the current/target state body text", () => {
    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Current state: ARMED. This will change it to: TRIPPED."),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Trip Kill Switch" })).toBeTruthy();
  });

  it("disables confirm for empty, whitespace-only, and >500 char reasons; enables it for a valid reason", () => {
    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const confirmButton = screen.getByRole("button", { name: "Trip Kill Switch" });
    const reasonField = screen.getByLabelText("Reason");

    expect(confirmButton.hasAttribute("disabled")).toBe(true);

    fireEvent.change(reasonField, { target: { value: "   " } });
    expect(confirmButton.hasAttribute("disabled")).toBe(true);

    fireEvent.change(reasonField, { target: { value: "a".repeat(501) } });
    expect(confirmButton.hasAttribute("disabled")).toBe(true);

    fireEvent.change(reasonField, { target: { value: "drill" } });
    expect(confirmButton.hasAttribute("disabled")).toBe(false);
  });

  it("with requiresTypedConfirmation RESET: keeps confirm disabled until the typed field is exactly RESET", () => {
    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Reset Kill Switch"
        currentState="TRIPPED"
        targetState="ARMED"
        requiresTypedConfirmation="RESET"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const confirmButton = screen.getByRole("button", { name: "Reset Kill Switch" });
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });
    expect(confirmButton.hasAttribute("disabled")).toBe(true);

    const typedField = screen.getByLabelText("Type RESET to confirm");
    fireEvent.change(typedField, { target: { value: "reset" } });
    expect(confirmButton.hasAttribute("disabled")).toBe(true);

    fireEvent.change(typedField, { target: { value: "RESET" } });
    expect(confirmButton.hasAttribute("disabled")).toBe(false);
  });

  it("shows no typed-confirmation field when requiresTypedConfirmation is absent", () => {
    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Disable Strategy"
        currentState="ENABLED"
        targetState="DISABLED"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Type RESET to confirm")).toBeNull();
  });

  it("closes immediately on a changed:true response and calls onDone", async () => {
    const onClose = vi.fn();
    const onDone = vi.fn();
    const onConfirm = vi.fn<(reason: string) => Promise<ChangedResult>>().mockResolvedValue({
      ok: true,
      data: { changed: true },
      replayed: false,
      status: 200,
    });

    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={onConfirm}
        onClose={onClose}
        onDone={onDone}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();

    expect(onConfirm).toHaveBeenCalledWith("drill");
    expect(onClose).toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("on a changed:false response, stays open, shows the unchanged body, hides confirm, and switches dismiss to Close", async () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn<(reason: string) => Promise<ChangedResult>>().mockResolvedValue({
      ok: true,
      data: { changed: false },
      replayed: false,
      status: 200,
    });

    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();

    expect(
      screen.getByText("Already TRIPPED — no change (recorded)"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Trip Kill Switch" })).toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("on an error response, stays open and shows the mapped message", async () => {
    const onClose = vi.fn();
    const onDone = vi.fn();
    const onConfirm = vi.fn<(reason: string) => Promise<ChangedResult>>().mockResolvedValue({
      ok: false,
      status: 422,
      code: "invalid_control_reason",
      message: "Reason is required and must be 500 characters or fewer.",
      detail: null,
    });

    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={onConfirm}
        onClose={onClose}
        onDone={onDone}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();

    expect(
      screen.getByText("Reason is required and must be 500 characters or fewer."),
    ).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("calls onDone after every ok result (changed true or false), but not on error", async () => {
    const onDone = vi.fn();
    const onConfirm = vi.fn<(reason: string) => Promise<ChangedResult>>().mockResolvedValue({
      ok: true,
      data: { changed: false },
      replayed: false,
      status: 200,
    });

    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={onConfirm}
        onClose={vi.fn()}
        onDone={onDone}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();

    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("dismiss button reads Keep Current State before submission, and Escape closes", () => {
    const onClose = vi.fn();
    render(
      <ControlConfirmDialog
        open={true}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={vi.fn()}
        onClose={onClose}
      />,
    );

    expect(screen.getByRole("button", { name: "Keep Current State" })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  describe("WR-C-01: dismissal while a control request is in flight", () => {
    type Deferred = {
      promise: Promise<ChangedResult>;
      resolve: (value: ChangedResult) => void;
    };

    function deferred(): Deferred {
      let resolve!: (value: ChangedResult) => void;
      const promise = new Promise<ChangedResult>((res) => {
        resolve = res;
      });
      return { promise, resolve };
    }

    function renderDialog(
      props: Partial<ComponentProps<typeof ControlConfirmDialog>>,
    ) {
      const base = {
        open: true,
        actionLabel: "Trip Kill Switch",
        currentState: "ARMED",
        targetState: "TRIPPED",
        onConfirm: vi.fn(),
        onClose: vi.fn(),
      };
      return render(<ControlConfirmDialog {...base} {...props} />);
    }

    it("disables the dismiss button and ignores Escape while submitting", async () => {
      const pending = deferred();
      const onClose = vi.fn();
      renderDialog({ onConfirm: () => pending.promise, onClose });

      fireEvent.change(screen.getByLabelText("Reason"), {
        target: { value: "drill" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
      await flush();

      const dismiss = screen.getByRole("button", { name: "Keep Current State" });
      expect(dismiss.hasAttribute("disabled")).toBe(true);

      fireEvent.keyDown(document, { key: "Escape" });
      expect(onClose).not.toHaveBeenCalled();

      await act(async () => {
        pending.resolve({
          ok: true,
          data: { changed: true },
          replayed: false,
          status: 200,
        });
        await Promise.resolve();
      });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("does not apply a stale response to a re-opened dialog, but still reports onDone", async () => {
      const pending = deferred();
      const onClose = vi.fn();
      const onDone = vi.fn();
      const onConfirm = vi.fn(() => pending.promise);
      const props = {
        actionLabel: "Trip Kill Switch",
        currentState: "ARMED",
        targetState: "TRIPPED",
        onConfirm,
        onClose,
        onDone,
      };
      const { rerender } = render(<ControlConfirmDialog open={true} {...props} />);

      fireEvent.change(screen.getByLabelText("Reason"), {
        target: { value: "drill" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
      await flush();

      // The parent closes and re-opens the dialog while the PUT is in flight.
      rerender(<ControlConfirmDialog open={false} {...props} />);
      rerender(<ControlConfirmDialog open={true} {...props} />);

      await act(async () => {
        pending.resolve({
          ok: true,
          data: { changed: false },
          replayed: false,
          status: 200,
        });
        await Promise.resolve();
      });

      expect(onDone).toHaveBeenCalledTimes(1);
      expect(onClose).not.toHaveBeenCalled();
      expect(screen.queryByText(/Already TRIPPED/)).toBeNull();
      expect(
        screen.getByRole("button", { name: "Trip Kill Switch" }),
      ).toBeTruthy();
    });
  });

  describe("WR-C-02: re-verification after an ambiguous control failure", () => {
    async function submitWith(result: ChangedResult, onOutcomeUncertain: () => void, onDone = vi.fn()) {
      render(
        <ControlConfirmDialog
          open={true}
          actionLabel="Trip Kill Switch"
          currentState="ARMED"
          targetState="TRIPPED"
          onConfirm={vi.fn().mockResolvedValue(result)}
          onClose={vi.fn()}
          onDone={onDone}
          onOutcomeUncertain={onOutcomeUncertain}
        />,
      );
      fireEvent.change(screen.getByLabelText("Reason"), {
        target: { value: "drill" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
      await flush();
    }

    const failure = (status: number | null): ChangedResult => ({
      ok: false,
      status,
      code: null,
      message: "failed",
      detail: null,
    });

    it("calls onOutcomeUncertain (not onDone) after a transport failure", async () => {
      const onOutcomeUncertain = vi.fn();
      const onDone = vi.fn();
      await submitWith(failure(null), onOutcomeUncertain, onDone);
      expect(onOutcomeUncertain).toHaveBeenCalledTimes(1);
      expect(onDone).not.toHaveBeenCalled();
      expect(screen.getByText("failed")).toBeTruthy();
    });

    it("calls onOutcomeUncertain after a 5xx", async () => {
      const onOutcomeUncertain = vi.fn();
      await submitWith(failure(503), onOutcomeUncertain);
      expect(onOutcomeUncertain).toHaveBeenCalledTimes(1);
    });

    it("does not call onOutcomeUncertain after a definitive 4xx rejection", async () => {
      const onOutcomeUncertain = vi.fn();
      await submitWith(failure(422), onOutcomeUncertain);
      expect(onOutcomeUncertain).not.toHaveBeenCalled();
    });
  });

  describe("WR-C-05: accessibility", () => {
    function Harness({
      onConfirm = vi.fn(),
    }: {
      onConfirm?: (reason: string) => Promise<ChangedResult>;
    }) {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            opener
          </button>
          <ControlConfirmDialog
            open={open}
            actionLabel="Reset Kill Switch"
            currentState="TRIPPED"
            targetState="ARMED"
            requiresTypedConfirmation="RESET"
            onConfirm={onConfirm}
            onClose={() => setOpen(false)}
          />
        </>
      );
    }

    it("moves focus to the reason field on open and restores it to the opener on close", () => {
      render(<Harness />);
      const opener = screen.getByRole("button", { name: "opener" });
      opener.focus();
      fireEvent.click(opener);

      expect(document.activeElement).toBe(screen.getByLabelText("Reason"));

      fireEvent.click(screen.getByRole("button", { name: "Keep Current State" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(opener);
    });

    it("traps Tab and Shift+Tab inside the dialog", () => {
      render(<Harness />);
      fireEvent.click(screen.getByRole("button", { name: "opener" }));

      // Enable the confirm button so every control is focusable.
      fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });
      fireEvent.change(screen.getByLabelText("Type RESET to confirm"), {
        target: { value: "RESET" },
      });
      const dismiss = screen.getByRole("button", { name: "Keep Current State" });
      const confirm = screen.getByRole("button", { name: "Reset Kill Switch" });
      const reason = screen.getByLabelText("Reason");

      confirm.focus();
      const forward = fireEvent.keyDown(document, { key: "Tab" });
      expect(forward).toBe(false); // default prevented
      expect(document.activeElement).toBe(reason);

      reason.focus();
      const backward = fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
      expect(backward).toBe(false);
      expect(document.activeElement).toBe(confirm);

      // Focus that escaped the dialog is pulled back in.
      screen.getByRole("button", { name: "opener" }).focus();
      fireEvent.keyDown(document, { key: "Tab" });
      expect(document.activeElement).toBe(reason);
      expect(dismiss).toBeTruthy();
    });

    it("leaves Tab between inner controls alone", () => {
      render(<Harness />);
      fireEvent.click(screen.getByRole("button", { name: "opener" }));
      screen.getByLabelText("Reason").focus();
      const notPrevented = fireEvent.keyDown(document, { key: "Tab" });
      expect(notPrevented).toBe(true);
    });

    it("announces a failed request via role=alert", async () => {
      const onConfirm = vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        code: "invalid_control_reason",
        message: "Reason is required and must be 500 characters or fewer.",
        detail: null,
      });
      render(<Harness onConfirm={onConfirm} />);
      fireEvent.click(screen.getByRole("button", { name: "opener" }));
      fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });
      fireEvent.change(screen.getByLabelText("Type RESET to confirm"), {
        target: { value: "RESET" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Reset Kill Switch" }));
      await flush();

      expect(screen.getByRole("alert").textContent).toBe(
        "Reason is required and must be 500 characters or fewer.",
      );
    });

    it("announces the unchanged notice via role=alert and moves focus to Close", async () => {
      const onConfirm = vi.fn().mockResolvedValue({
        ok: true,
        data: { changed: false },
        replayed: false,
        status: 200,
      });
      render(<Harness onConfirm={onConfirm} />);
      fireEvent.click(screen.getByRole("button", { name: "opener" }));
      fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });
      fireEvent.change(screen.getByLabelText("Type RESET to confirm"), {
        target: { value: "RESET" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Reset Kill Switch" }));
      await flush();

      expect(screen.getByRole("alert").textContent).toBe(
        "Already ARMED — no change (recorded)",
      );
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
    });

    it("ties the body copy and helper texts to the dialog and fields via aria-describedby", () => {
      render(<Harness />);
      fireEvent.click(screen.getByRole("button", { name: "opener" }));

      const describedText = (element: HTMLElement) =>
        (element.getAttribute("aria-describedby") ?? "")
          .split(" ")
          .map((id) => document.getElementById(id)?.textContent)
          .join(" ");

      expect(describedText(screen.getByRole("dialog"))).toBe(
        "Current state: TRIPPED. This will change it to: ARMED.",
      );
      expect(describedText(screen.getByLabelText("Reason"))).toBe(
        "Required, up to 500 characters",
      );
      expect(describedText(screen.getByLabelText("Type RESET to confirm"))).toBe(
        "Resetting the kill switch allows trading to resume.",
      );
    });
  });

  it("renders nothing when open is false", () => {
    render(
      <ControlConfirmDialog
        open={false}
        actionLabel="Trip Kill Switch"
        currentState="ARMED"
        targetState="TRIPPED"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

type FirstFrame = {
  reason: string | null;
  typed: string | null;
  confirmDisabled: boolean | null;
  alerts: number;
  already: boolean;
  bodyText: string | null;
  dismissLabel: string | null;
};

/**
 * UAT gap 3: RTL render/rerender are wrapped in act, which flushes passive
 * effects before any assertion, so a stale first frame that a passive-effect
 * reset repairs is invisible to ordinary assertions. This probe is rendered as
 * the NEXT SIBLING of the dialog; its useLayoutEffect runs in the same commit
 * as the dialog, before any passive effect, and snapshots the DOM of only the
 * first commit after each false -> true flip of `open`.
 */
function FrameProbe({
  open,
  actionLabel,
  frames,
}: {
  open: boolean;
  actionLabel: string;
  frames: FirstFrame[];
}) {
  const wasOpen = useRef(false);
  useLayoutEffect(() => {
    if (open && !wasOpen.current) {
      const reason = document.getElementById(
        "control-confirm-dialog-reason",
      ) as HTMLTextAreaElement | null;
      const typed = document.getElementById(
        "control-confirm-dialog-typed-confirmation",
      ) as HTMLInputElement | null;
      const buttons = Array.from(
        document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button'),
      );
      const confirm = buttons.find((b) => b.textContent === actionLabel);
      frames.push({
        reason: reason ? reason.value : null,
        typed: typed ? typed.value : null,
        confirmDisabled: confirm ? confirm.disabled : null,
        alerts: document.querySelectorAll('[role="alert"]').length,
        already: (document.body.textContent ?? "").includes("Already"),
        bodyText:
          document.getElementById("control-confirm-dialog-body")?.textContent ??
          null,
        dismissLabel: buttons[0]?.textContent ?? null,
      });
    }
    wasOpen.current = open;
  });
  return null;
}

describe("UAT gap 3: every opening is clean on its first committed frame", () => {
  function GapHarness({
    onConfirm,
    frames,
    requiresTypedConfirmation,
    actionLabel = "Trip Kill Switch",
  }: {
    onConfirm: (reason: string) => Promise<ChangedResult>;
    frames: FirstFrame[];
    requiresTypedConfirmation?: "RESET";
    actionLabel?: string;
  }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          opener
        </button>
        <ControlConfirmDialog
          open={open}
          actionLabel={actionLabel}
          currentState="ARMED"
          targetState="TRIPPED"
          requiresTypedConfirmation={requiresTypedConfirmation}
          onConfirm={onConfirm}
          onClose={() => setOpen(false)}
        />
        <FrameProbe open={open} actionLabel={actionLabel} frames={frames} />
      </>
    );
  }

  const CLEAN_PROMPT = "Current state: ARMED. This will change it to: TRIPPED.";

  function expectCleanFrame(frame: FirstFrame | undefined) {
    expect(frame).toBeTruthy();
    expect(frame?.reason).toBe("");
    expect(frame?.confirmDisabled).toBe(true);
    expect(frame?.alerts).toBe(0);
    expect(frame?.already).toBe(false);
    expect(frame?.bodyText).toBe(CLEAN_PROMPT);
    expect(frame?.dismissLabel).toBe("Keep Current State");
  }

  function reopen() {
    fireEvent.click(screen.getByRole("button", { name: "opener" }));
  }

  function fillReason(value = "drill") {
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value } });
  }

  it("(a) typed reason, then Keep Current State", () => {
    const frames: FirstFrame[] = [];
    render(<GapHarness onConfirm={vi.fn()} frames={frames} />);
    reopen();
    fillReason();
    fireEvent.click(screen.getByRole("button", { name: "Keep Current State" }));
    reopen();

    expect(frames).toHaveLength(2);
    expectCleanFrame(frames[1]);
  });

  it("(b) changed:false unchanged notice, then Close", async () => {
    const frames: FirstFrame[] = [];
    const onConfirm = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { changed: false }, replayed: false, status: 200 });
    render(<GapHarness onConfirm={onConfirm} frames={frames} />);
    reopen();
    fillReason();
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("Already TRIPPED — no change (recorded)");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    reopen();

    expect(frames).toHaveLength(2);
    expectCleanFrame(frames[1]);
  });

  it("(c) changed:true close", async () => {
    const frames: FirstFrame[] = [];
    const onConfirm = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { changed: true }, replayed: false, status: 200 });
    render(<GapHarness onConfirm={onConfirm} frames={frames} />);
    reopen();
    fillReason();
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();
    expect(screen.queryByRole("dialog")).toBeNull();
    reopen();

    expect(frames).toHaveLength(2);
    expectCleanFrame(frames[1]);
  });

  it("(d) error response, then Keep Current State", async () => {
    const frames: FirstFrame[] = [];
    const onConfirm = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      code: "invalid_control_request",
      message: "Request rejected",
      detail: null,
    });
    render(<GapHarness onConfirm={onConfirm} frames={frames} />);
    reopen();
    fillReason();
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("Request rejected");
    fireEvent.click(screen.getByRole("button", { name: "Keep Current State" }));
    reopen();

    expect(frames).toHaveLength(2);
    expectCleanFrame(frames[1]);
  });

  it("(e) RESET typed field filled, then Keep Current State", () => {
    const frames: FirstFrame[] = [];
    render(
      <GapHarness
        onConfirm={vi.fn()}
        frames={frames}
        requiresTypedConfirmation="RESET"
      />,
    );
    reopen();
    fillReason();
    fireEvent.change(screen.getByLabelText("Type RESET to confirm"), {
      target: { value: "RESET" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Keep Current State" }));
    reopen();

    expect(frames).toHaveLength(2);
    expectCleanFrame(frames[1]);
    expect(frames[1]?.typed).toBe("");
  });

  describe.each([
    ["plain", false],
    ["StrictMode", true],
  ])("focus after unchanged -> Close -> re-open (%s)", (_name, strict) => {
    it("lands on the Reason textarea on the re-opened dialog", async () => {
      const frames: FirstFrame[] = [];
      const onConfirm = vi
        .fn()
        .mockResolvedValue({ ok: true, data: { changed: false }, replayed: false, status: 200 });
      const tree = <GapHarness onConfirm={onConfirm} frames={frames} />;
      render(strict ? <StrictMode>{tree}</StrictMode> : tree);
      reopen();
      fillReason();
      fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
      await flush();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      reopen();
      await flush();

      expect(document.activeElement).toBe(screen.getByLabelText("Reason"));
    });
  });
});

describe("WR-C-01: mount sites never key a dialog shell", () => {
  const COMPONENTS_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

  /**
   * Returns each JSX opening tag `<Name ...>` in the source. Walks characters
   * from `<Name`, tracking `{`/`}` depth, and stops at the first `>` at depth 0
   * (a lazy regex would stop inside the `=>` of an arrow-function prop).
   */
  function openingTags(source: string, name: string): string[] {
    const tags: string[] = [];
    const startRe = new RegExp(`<${name}(?![A-Za-z0-9_])`, "g");
    let match: RegExpExecArray | null;
    while ((match = startRe.exec(source)) !== null) {
      let depth = 0;
      let i = match.index;
      for (; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === "{") depth += 1;
        else if (ch === "}") depth -= 1;
        else if (ch === ">" && depth === 0 && source[i - 1] !== "=") break;
      }
      tags.push(source.slice(match.index, i + 1));
    }
    return tags;
  }

  it("the scanner stops at the tag end, not at an arrow-function prop", () => {
    const tags = openingTags(
      `<Foo onClose={() => setOpen(false)} open={x} />\n<div key={1} />`,
      "Foo",
    );
    expect(tags).toHaveLength(1);
    expect(tags[0]).toContain("open={x}");
    expect(tags[0]).not.toContain("key=");
  });

  const SITES: Array<[string, string, string]> = [
    ["controls/KillSwitchControlTrigger.tsx", "ControlConfirmDialog", "one"],
    ["controls/StrategyControlTrigger.tsx", "ControlConfirmDialog", "one"],
    ["jobs/detail/JobHeaderPanel.tsx", "CancelJobDialog", "one"],
    ["jobs/detail/JobHeaderPanel.tsx", "RetryJobDialog", "one"],
  ];

  it.each(SITES)("%s mounts exactly one <%s> and never keys it", (file, name) => {
    const source = readFileSync(join(COMPONENTS_ROOT, file), "utf8");
    const tags = openingTags(source, name);
    expect(tags).toHaveLength(1);
    expect(/\bkey=/.test(tags[0])).toBe(false);
  });
});

describe("controlEvents", () => {
  it("dispatchControlChanged('killswitch') fires a window CustomEvent 'killswitch:changed'", () => {
    const handler = vi.fn();
    window.addEventListener(KILL_SWITCH_CHANGED_EVENT, handler);
    dispatchControlChanged("killswitch");
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener(KILL_SWITCH_CHANGED_EVENT, handler);
  });

  it("useControlChanged('strategy', handler) invokes handler on 'strategy:changed' and unsubscribes on unmount", () => {
    const handler = vi.fn();

    function Probe() {
      useControlChanged("strategy", handler);
      return null;
    }

    const { unmount } = render(<Probe />);

    act(() => {
      window.dispatchEvent(new CustomEvent(STRATEGY_CHANGED_EVENT));
    });
    expect(handler).toHaveBeenCalledTimes(1);

    unmount();

    act(() => {
      window.dispatchEvent(new CustomEvent(STRATEGY_CHANGED_EVENT));
    });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("useControlChanged does not cross-fire on the other domain's event", () => {
    const handler = vi.fn();

    function Probe() {
      useControlChanged("strategy", handler);
      return null;
    }

    render(<Probe />);

    act(() => {
      window.dispatchEvent(new CustomEvent(KILL_SWITCH_CHANGED_EVENT));
    });
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("StrategyStatusBadge", () => {
  it("renders ENABLED with emerald classes", () => {
    render(<StrategyStatusBadge enabled={true} />);
    const badge = screen.getByText("ENABLED");
    expect(badge.className).toContain("emerald");
  });

  it("renders DISABLED with zinc classes", () => {
    render(<StrategyStatusBadge enabled={false} />);
    const badge = screen.getByText("DISABLED");
    expect(badge.className).toContain("zinc");
    expect(badge.className).not.toContain("emerald");
  });
});
