---
phase: 13-console-foundation-and-system-status
plan: 02
subsystem: ui
tags: [nextjs, tailwind, proxy, react, typescript]

# Dependency graph
requires:
  - phase: 13-01
    provides: GET /api/v1/system/kill-switch route consumed by later console screens
provides:
  - Next.js 16 app shell at console/ (App Router, TS, Tailwind, src dir)
  - Env-driven /backend/:path* rewrite proxy to the FastAPI read surface (no CORS)
  - console/.env.example + .env.local pattern for TRADING_CONSOLE_API_BASE_URL
  - Dark utilitarian layout with top nav and KillSwitchBanner mount placeholder
  - Single documented start command (make console) and console/README.md
affects: [13-03-kill-switch-banner, 13-04-system-status-screen, 14-runs-and-strategies, 16-analytics-and-charting]

# Tech tracking
tech-stack:
  added: [next@16.2.10, react@19.2.4, tailwindcss@4, typescript@5]
  patterns:
    - "All browser calls to the FastAPI backend go through Next.js rewrites under /backend/* instead of direct cross-origin fetches, since the FastAPI app has no CORS middleware and none is authorized."
    - "Backend base URL is read once in next.config.ts from process.env.TRADING_CONSOLE_API_BASE_URL (falls back to http://127.0.0.1:8000), never hardcoded in components."

key-files:
  created:
    - console/next.config.ts
    - console/.env.example
    - console/README.md
    - console/src/app/layout.tsx
    - console/src/app/page.tsx
    - console/src/app/globals.css
  modified:
    - Makefile
    - README.md

key-decisions:
  - "npm rejects a package named 'console' (reserved core-module name), so create-next-app scaffolded into a temp directory (operator-console-scaffold) which was then moved to console/ and package.json's name field changed to operator-console; the console/ folder name itself is unaffected."
  - "create-next-app's generated .gitignore in this Next.js version uses a blanket '.env*' rule (older templates used '.env*.local'), which would have silently excluded the required .env.example from version control; added a '!.env.example' negation."
  - "Added turbopack.root to next.config.ts to pin the workspace root, since an unrelated stray package-lock.json in the operator's home directory made Next.js misdetect the monorepo root and emit a warning on every build."

patterns-established:
  - "Pattern: proxy-all-backend-calls-through-nextjs-rewrites — every later console screen fetches from /backend/... not the FastAPI host directly."

requirements-completed: [CONS-01]

# Metrics
duration: ~20min
completed: 2026-07-08
---

# Phase 13 Plan 02: Console Foundation Scaffold Summary

**Next.js 16 operator console scaffolded at console/ with a next.config.ts rewrite proxy (/backend/:path* -> TRADING_CONSOLE_API_BASE_URL) that lets the browser reach the FastAPI read surface with zero CORS configuration, plus a dark nav shell and `make console` as the single documented start command.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files modified/created:** 19 (15 new console/ scaffold files + gitignore/README fixes, plus Makefile and root README.md)

## Accomplishments
- Working Next.js 16 app at `console/` (App Router, TypeScript, Tailwind, src dir) that builds cleanly with `npm run build`
- `next.config.ts` rewrites `/backend/:path*` to the FastAPI base URL read from `TRADING_CONSOLE_API_BASE_URL` (defaults to `http://127.0.0.1:8000`); verified end-to-end with a live FastAPI instance — `curl http://localhost:3000/backend/health` (Next dev server) returned the real `/health` JSON with no CORS involved
- Dark, utilitarian layout with a slim top nav ("Operator Console" / "System Status") and an explicit `{/* KillSwitchBanner mounts here (plan 13-03) ... */}` placeholder directly under the nav
- `console/README.md` documents prereqs, `.env.local` setup, and the proxy design; root `README.md` gained an "Operator Console" section
- `Makefile` gained `console` (npm run dev) and `console-install` (npm install) targets

## Task Commits

1. **Task 1: Scaffold Next.js app with env-driven backend proxy** - `75e2753` (feat)
2. **Task 2: App shell, nav, and the single documented start command** - `080c887` (feat)

**Plan metadata:** (this commit, docs)

## Files Created/Modified
- `console/package.json` - Next.js app; name changed from scaffold temp name to `operator-console`
- `console/next.config.ts` - `/backend/:path*` rewrite proxy reading `TRADING_CONSOLE_API_BASE_URL`; `turbopack.root` pin
- `console/.env.example` - documented env template (committed)
- `console/.gitignore` - narrowed blanket `.env*` ignore with `!.env.example` so the template stays tracked
- `console/src/app/layout.tsx` - dark shell, top nav, KillSwitchBanner mount placeholder
- `console/src/app/page.tsx` - placeholder for the 13-04 system status screen
- `console/src/app/globals.css` - dark-only theme (dropped the light/dark `prefers-color-scheme` toggle from the scaffold)
- `console/README.md` - CONS-01 documentation (prereqs, setup, start, proxy design)
- `Makefile` - `console`, `console-install` targets
- `README.md` - Operator Console section

## Decisions Made
- Worked around npm's rejection of a package named "console" by scaffolding into a temp directory and renaming, rather than switching the folder name (plan required `console/` specifically)
- Narrowed the create-next-app-generated `.gitignore`'s `.env*` rule so `.env.example` stays committed, since the artifact requirement (`console/.env.example` tracked) conflicts with the newer template's blanket ignore
- Added `turbopack.root` to next.config.ts to silence a Next.js workspace-root misdetection warning caused by an unrelated file in the operator's home directory (out-of-repo, out of scope to fix directly)
- Removed the `prefers-color-scheme` light/dark CSS variable flip since the console is deliberately dark-only, not theme-adaptive

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] npm rejects "console" as a package name**
- **Found during:** Task 1 (create-next-app scaffold)
- **Issue:** `npx create-next-app@latest console ...` failed with "console is a core module name" — npm naming restriction, not something `create-next-app` flags let you override.
- **Fix:** Scaffolded into `operator-console-scaffold/`, moved the directory to `console/`, and changed `package.json`'s `name` field to `operator-console`. Directory path (what the plan and Makefile/README reference) is unaffected.
- **Files modified:** console/package.json
- **Verification:** `cd console && npm run build` succeeds; directory is `console/` as required.
- **Committed in:** 75e2753 (Task 1 commit)

**2. [Rule 3 - Blocking] Generated .gitignore would have excluded the required .env.example**
- **Found during:** Task 1 (creating .env.example)
- **Issue:** This Next.js version's create-next-app template ships `.gitignore` with a blanket `.env*` rule (older templates used `.env*.local`), which the plan's must-haves assumed would only ignore local overrides. As written it would silently drop `console/.env.example` from version control, failing the CONS-01 artifact requirement.
- **Fix:** Added `!.env.example` negation immediately after `.env*` in `console/.gitignore`.
- **Files modified:** console/.gitignore
- **Verification:** `git add --dry-run console` showed `add 'console/.env.example'`; `git check-ignore -v console/.env.local` confirmed `.env.local` is still ignored.
- **Committed in:** 75e2753 (Task 1 commit)

**3. [Rule 3 - Blocking-adjacent] Stray home-directory lockfile caused a Next.js workspace-root warning**
- **Found during:** Task 1 (`npm run build`)
- **Issue:** `npm run build` emitted a warning that Next.js inferred the workspace root as `/Users/yoavhevroni` due to an unrelated `package-lock.json` there, and recommended setting `turbopack.root`.
- **Fix:** Added `turbopack: { root: path.join(__dirname) }` to `console/next.config.ts` to pin the root explicitly. Did not touch the unrelated file in the home directory (out of scope, outside the repo).
- **Files modified:** console/next.config.ts
- **Verification:** Re-ran `npm run build`; warning no longer appears.
- **Committed in:** 75e2753 (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (3 blocking/environment-mismatch, 0 architectural)
**Impact on plan:** All three were necessary to satisfy CONS-01's stated artifacts (working scaffold at `console/`, tracked `.env.example`) and to keep the build output clean. No scope creep — no feature or screen work beyond what Task 1/2 specified.

## Issues Encountered
None beyond the deviations above.

## User Setup Required
None - no external service configuration required. `console/.env.local` was created locally from `.env.example` for the executor's own smoke test; it is gitignored and each operator creates their own copy per the README.

## Next Phase Readiness
- `console/` builds and runs; the `/backend/*` proxy was verified live against a running FastAPI instance (`curl http://localhost:<port>/backend/health` returned the real health payload)
- Layout has the explicit KillSwitchBanner mount placeholder plan 13-03 needs
- Page placeholder at `/` is ready for plan 13-04's system status screen
- No blockers for 13-03/13-04

---
*Phase: 13-console-foundation-and-system-status*
*Completed: 2026-07-08*

## Self-Check: PASSED

All created files verified present on disk (console/package.json, next.config.ts, .env.example, README.md, src/app/layout.tsx, page.tsx, globals.css, Makefile, root README.md, this SUMMARY.md). Both task commits (75e2753, 080c887) verified present in git log.
