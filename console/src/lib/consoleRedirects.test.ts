// Research pivot (proposal 00 Part L, plan §15): the research platform is the product.
// Pins (1) the legacy-URL redirect table next.config.ts applies, (2) that the primary
// navigation is the research product (Strategies, Assets, Studies, Jobs) with no trading
// entry and no kill-switch banner in the root layout, and (3) that the root page redirects.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LEGACY_REDIRECTS } from "./legacyRedirects";
import { HOME_HREF, PRIMARY_NAVIGATION, PRODUCT_NAME } from "./navigation";

const __dirname = dirname(fileURLToPath(import.meta.url)); // console/src/lib
const APP_ROOT = join(__dirname, "..", "app");

describe("legacy console URLs redirect to research pages", () => {
  it("maps every former trading page to a research page and leaves /jobs alone", () => {
    const table = Object.fromEntries(LEGACY_REDIRECTS.map((r) => [r.source, r.destination]));
    expect(table).toEqual({
      "/": "/research/studies",
      "/strategy": "/research/strategies",
      "/runs": "/research/studies",
      "/runs/:runId": "/research/studies",
      "/paper": "/research",
      "/controls": "/research",
    });
    expect(LEGACY_REDIRECTS.some((r) => r.source.startsWith("/jobs"))).toBe(false);
    expect(LEGACY_REDIRECTS.some((r) => r.source.startsWith("/research"))).toBe(false);
  });

  it("next.config.ts applies that table as permanent redirects and allows both local hosts", () => {
    const config = readFileSync(join(__dirname, "..", "..", "next.config.ts"), "utf8");
    expect(config).toContain('from "./src/lib/legacyRedirects"');
    expect(config).toMatch(/LEGACY_REDIRECTS\.map\(\(entry\) => \(\{ \.\.\.entry, permanent: true \}\)\)/);
    expect(config).toMatch(/allowedDevOrigins:\s*\["localhost",\s*"127\.0\.0\.1"\]/);
  });

  it("the root page redirects to the studies page", () => {
    const page = readFileSync(join(APP_ROOT, "page.tsx"), "utf8");
    expect(page).toContain("redirect(HOME_HREF)");
    expect(HOME_HREF).toBe("/research/studies");
  });
});

describe("primary navigation is the research product", () => {
  it("exposes Strategies, Assets, Studies and Jobs, in that order", () => {
    expect(PRIMARY_NAVIGATION.map((item) => [item.label, item.href])).toEqual([
      ["Strategies", "/research/strategies"],
      ["Assets", "/research/assets"],
      ["Studies", "/research/studies"],
      ["Jobs", "/jobs"],
    ]);
    expect(PRODUCT_NAME).toBe("Strategy Research");
  });

  it("the root layout renders that navigation and no trading chrome", () => {
    const layout = readFileSync(join(APP_ROOT, "layout.tsx"), "utf8");
    expect(layout).toContain("PRIMARY_NAVIGATION.map(");
    expect(layout).toContain("{PRODUCT_NAME}");
    for (const forbidden of ["KillSwitch", "Operator Console", "System Status", "Paper Trading", "Controls", '"/runs"', '"/strategy"', '"/paper"', '"/controls"']) {
      expect(layout, forbidden).not.toContain(forbidden);
    }
  });
});
