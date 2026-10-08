"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { KillSwitchBanner } from "@/components/KillSwitchBanner";
import { useResearchMode } from "@/lib/useResearchMode";

const SECTIONS = [
  { href: "/research/strategies", label: "Strategies" },
  { href: "/research/assets", label: "Assets" },
  { href: "/research/studies", label: "Studies" },
];

/**
 * Chrome of every research page: the section navigation and the API-mode gate. Against a
 * trading-mode API the research routes do not exist, so the page says so instead of
 * rendering endpoint-named 404s; while the mode is unknown the content still renders and
 * each panel reports its own fetch outcome honestly.
 */
export function ResearchGate({ title, children }: { title: string; children: ReactNode }) {
  const mode = useResearchMode();
  return (
    <main className="flex-1 p-6">
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h1 className="text-xl font-semibold text-zinc-100">{title}</h1>
        <nav className="flex gap-3 text-sm">
          {SECTIONS.map((section) => (
            <Link key={section.href} href={section.href} className="text-zinc-400 hover:text-zinc-100">
              {section.label}
            </Link>
          ))}
        </nav>
      </div>
      {mode.state === "trading" ? (
        <div className="rounded border border-amber-800 bg-amber-950/40 px-4 py-3 text-sm text-amber-100">
          <p className="font-semibold">This API runs in trading mode.</p>
          <p className="mt-1">
            The research section needs an API started with <code>TRADING_PLATFORM_RESEARCH__MODE=true</code> against
            the research database. No research route exists on this API.
          </p>
        </div>
      ) : (
        children
      )}
    </main>
  );
}

export function ResearchNavLink() {
  const mode = useResearchMode();
  if (mode.state !== "research") {
    return null;
  }
  return (
    <Link href="/research/studies" className="text-cyan-300 hover:text-cyan-100">
      Research
    </Link>
  );
}

/**
 * The global kill-switch banner belongs to the trading surface: a research-mode API
 * serves no `/api/v1/system/kill-switch` route, so rendering the banner there would show
 * a permanent "state UNKNOWN (404)" alarm that means nothing. Against a trading-mode or
 * not-yet-known API the banner renders exactly as before.
 */
export function KillSwitchBannerForMode() {
  const mode = useResearchMode();
  if (mode.state === "research") {
    return null;
  }
  return <KillSwitchBanner />;
}
