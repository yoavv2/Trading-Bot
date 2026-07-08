import path from "node:path";
import type { NextConfig } from "next";

const apiBaseUrl =
  process.env.TRADING_CONSOLE_API_BASE_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Pin the workspace root to this directory; an unrelated lockfile in the
  // operator's home directory would otherwise make Next.js guess wrong.
  turbopack: {
    root: path.join(__dirname),
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
