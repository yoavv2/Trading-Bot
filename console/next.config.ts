import path from "node:path";
import type { NextConfig } from "next";
import { LEGACY_REDIRECTS } from "./src/lib/legacyRedirects";

const apiBaseUrl =
  process.env.TRADING_CONSOLE_API_BASE_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Pin the workspace root to this directory; an unrelated lockfile in the
  // operator's home directory would otherwise make Next.js guess wrong.
  turbopack: {
    root: path.join(__dirname),
  },
  // The dev server only serves its HMR/RSC assets to origins it knows; opening the
  // console as 127.0.0.1 while it listens on localhost (or the reverse) otherwise
  // leaves the page server-rendered but never hydrated.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  // Legacy operator-console URLs -> research pages (src/lib/legacyRedirects.ts).
  async redirects() {
    return LEGACY_REDIRECTS.map((entry) => ({ ...entry, permanent: true }));
  },
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${apiBaseUrl.replace(/\/$/, "")}/:path*`,
      },
    ];
  },
};

export default nextConfig;
