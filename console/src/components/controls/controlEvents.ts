"use client";

import { useEffect } from "react";

/**
 * D-13/UI-SPEC "Keeping control state in sync": same-tab pub/sub for the
 * two safety-control domains. A mutation from one mounted display (e.g.
 * KillSwitchBanner) does not automatically refresh another (e.g.
 * KillSwitchPanel on /controls) — these two named window CustomEvents are
 * the minimal sync mechanism. No global store, SSE, or polling-interval
 * change is introduced.
 */
export const KILL_SWITCH_CHANGED_EVENT = "killswitch:changed";
export const STRATEGY_CHANGED_EVENT = "strategy:changed";

export type ControlDomain = "killswitch" | "strategy";

const DOMAIN_EVENT_NAME: Record<ControlDomain, string> = {
  killswitch: KILL_SWITCH_CHANGED_EVENT,
  strategy: STRATEGY_CHANGED_EVENT,
};

/**
 * Dispatches the named window CustomEvent for `domain`. Called immediately
 * after any control mutation resolves successfully (changed: true OR
 * changed: false — both are a confirmed successful call).
 */
export function dispatchControlChanged(domain: ControlDomain): void {
  window.dispatchEvent(new CustomEvent(DOMAIN_EVENT_NAME[domain]));
}

/**
 * Subscribes `handler` to `domain`'s change event for the lifetime of the
 * calling component; unsubscribes on unmount or when `domain`/`handler`
 * change identity.
 */
export function useControlChanged(
  domain: ControlDomain,
  handler: () => void,
): void {
  useEffect(() => {
    const eventName = DOMAIN_EVENT_NAME[domain];
    window.addEventListener(eventName, handler);
    return () => {
      window.removeEventListener(eventName, handler);
    };
  }, [domain, handler]);
}
