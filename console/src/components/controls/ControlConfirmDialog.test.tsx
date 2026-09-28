// @vitest-environment jsdom
import type { ComponentProps } from "react";
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
