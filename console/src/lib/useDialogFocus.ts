import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function focusableWithin(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/**
 * Focus management for the plain React-state modal overlays (WR-C-05), which
 * declare `aria-modal="true"` but are not native `<dialog>` elements (a
 * UI-SPEC constraint), so the browser gives none of it for free:
 *   - on open, focus moves into the panel (`initialFocusRef`, else the first
 *     focusable element, else the panel itself);
 *   - Tab / Shift+Tab wrap inside the panel (and pull focus back in if it
 *     ever sits outside, e.g. after the focused button became disabled);
 *   - on close (or unmount) focus returns to the element that opened it.
 * No dependencies; the app root is deliberately not made inert because the
 * overlay renders inside that same tree.
 */
export function useDialogFocus(
  open: boolean,
  panelRef: RefObject<HTMLElement | null>,
  initialFocusRef?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!open) {
      return;
    }
    const panel = panelRef.current;
    if (!panel) {
      return;
    }
    const opener =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const initial = initialFocusRef?.current ?? focusableWithin(panel)[0] ?? panel;
    initial.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Tab" || !panel) {
        return;
      }
      const focusable = focusableWithin(panel);
      if (focusable.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && panel.contains(active);
      if (event.shiftKey) {
        if (!inside || active === first || active === panel) {
          event.preventDefault();
          last.focus();
        }
      } else if (!inside || active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (opener && opener.isConnected) {
        opener.focus();
      }
    };
    // Runs per open/close transition only; the refs are stable objects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}
