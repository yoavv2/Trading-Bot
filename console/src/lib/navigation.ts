/**
 * Product navigation (research pivot, proposal 00 Part L; plan §15). The research
 * platform is the product: Strategies, Assets, Studies and the generic Jobs surface
 * the studies run on. The trading console (status, runs, paper, controls, the
 * kill-switch banner) is no longer part of the user-facing product; its legacy URLs
 * redirect to the research pages (next.config.ts) and its backend stays frozen.
 */
export const PRODUCT_NAME = "Strategy Research";

export const HOME_HREF = "/research/studies";

export const PRIMARY_NAVIGATION: ReadonlyArray<{ href: string; label: string }> = [
  { href: "/research/strategies", label: "Strategies" },
  { href: "/research/assets", label: "Assets" },
  { href: "/research/studies", label: "Studies" },
  { href: "/jobs", label: "Jobs" },
];
