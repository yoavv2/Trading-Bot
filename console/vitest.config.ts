import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Mirrors tsconfig.json's "@/*" -> "./src/*" path mapping. Next's webpack
  // build resolves this natively from tsconfig, but Vite/vitest does not —
  // without this alias, any component under test that imports via "@/..."
  // (the codebase's standard app/component import style, e.g. JobsTable.tsx)
  // fails to resolve under vitest, even though `next build`/`tsc` are clean.
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
