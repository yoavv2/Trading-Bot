---
phase: 18
slug: orchestration-surface
status: draft
shadcn_initialized: false
preset: not applicable
created: 2026-07-21
---

# Phase 18 — UI Design Contract

> Backend-only interaction contract. Phase 18 creates an HTTP API and CLI boundary; it creates no browser UI. Console triggers and operator-facing screens are explicitly deferred to Phase 19, and audit/status views to Phase 21.

---

## Scope Fence

**This phase MUST NOT modify or add** console routes, pages, components, navigation, browser fetch hooks, forms, dialogs, client polling/push behavior, styling, design tokens, typography, palette, icons, or shadcn components.

The existing Next.js console is out of scope. `POST /api/v1/jobs` and `POST /api/v1/jobs/{job_id}/cancel` are machine/operator HTTP contracts for future clients; they are not authorization to add console controls now.

**Source:** 18-CONTEXT.md D-01/D-02 and Deferred Ideas; 18-RESEARCH.md Architecture Responsibility Map; ROADMAP.md Phase 18–19.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none — no frontend implementation in this phase |
| Preset | not applicable |
| Component library | not applicable |
| Icon library | not applicable |
| Font | not applicable |

**shadcn gate:** Not applicable. A Next.js console exists under `console/`, but Phase 18 cannot touch it. Do not initialize shadcn or change its design system for this backend-only phase.

---

## Spacing Scale

Visual spacing is **not applicable**. No rendered UI, layout, control, or touch target is in Phase 18 scope.

No spacing tokens or exceptions are declared because declaring them would create an unauthorized frontend contract.

---

## Typography

Typography is **not applicable**. No browser-visible text is introduced in Phase 18; API response schemas and CLI diagnostics are not visual typography.

---

## Color

Color is **not applicable**. Phase 18 adds no rendered surfaces, semantic color states, badges, or action controls.

No 60/30/10 palette is declared. Color ownership remains with the existing console and any Phase 19 trigger UI.

---

## HTTP Interaction Contract

### Mutation endpoints

| Interaction | Request contract | Success contract | Source |
|-------------|------------------|------------------|--------|
| Submit generic Job | `POST /api/v1/jobs`; registered `job_type`; validated JSON payload; required `Idempotency-Key` header | New request: `202` plus compact Job reference. Exact replay: `200`, `Idempotency-Replayed: true`, and the same Job identity/reference schema. | CONTEXT D-04–D-10, D-16–D-19 |
| Cancel Job | `POST /api/v1/jobs/{job_id}/cancel`; optional trimmed `reason` (blank becomes `null`, maximum 500 characters); required `Idempotency-Key` header | Return the updated compact Job reference. Queued cancellation is immediate; running cancellation is cooperative. | CONTEXT D-11–D-14, D-20 |

### Compact Job reference

Every successful mutation returns exactly this compact shape; it must not include progress snapshots, logs, or events.

| Field | Contract |
|-------|----------|
| `job_id` | Persisted Job identifier |
| `job_type` | Registered Job type |
| `status` | Point-in-time Job lifecycle status |
| `links.self` | Relative `/api/v1/jobs/{job_id}` path |
| `links.progress` | Relative `/api/v1/jobs/{job_id}/progress` path |
| `links.logs` | Relative `/api/v1/jobs/{job_id}/logs` path |
| `links.events` | Relative `/api/v1/jobs/{job_id}/events` path |

Clients observe evolving state exclusively through the linked existing read endpoints. Links must remain relative; do not derive absolute URLs from host or proxy headers, and do not impose polling or push transport behavior.

**Source:** CONTEXT D-16–D-20; RESEARCH.md Pattern 2.

### Required rejection and repeat semantics

| Condition | Required HTTP result | Mutation rule |
|-----------|----------------------|---------------|
| Missing `Idempotency-Key` | Typed `400` | No mutation. |
| Unregistered `job_type` | Typed `422` | Reject before Job persistence; no knowingly unexecutable Job. |
| Same endpoint/key, same canonical operation identity | Original Job/reference contract (`200` plus replay header for submission) | No second Job or operation execution. |
| Same endpoint/key, different canonical operation identity | Typed `409` with stable machine-readable error code and original Job ID | No mutation. |
| Cancellation reason over 500 characters | Validation rejection before mutation | No cancellation/idempotency state write. |
| Cancellation target absent | `404` | No mutation. |
| Fresh cancellation of `SUCCEEDED` or `FAILED` Job | Typed `409` including current Job status | No mutation. |
| Repeat cancellation already requested or after `CANCELLED` | Current compact Job reference | Preserve first requester/reason/audit facts; do not overwrite. |

Canonical identity is endpoint-scoped: submission fingerprints normalized `job_type` plus payload; cancellation fingerprints target Job plus normalized reason. The same header value may be used independently on different mutation routes.

**Source:** CONTEXT D-06–D-15; RESEARCH.md Patterns 1 and 3.

### Adapter boundary

FastAPI routes parse HTTP input and map typed outcomes only. CLI adapters retain worker-infrastructure commands such as `run-jobs` only; direct manual-operation CLI triggers are removed. API routes and retained CLI adapters must delegate to shared services/framework behavior and contain no duplicated business logic.

The only execution proof in this phase is a test-only registered handler: submit → queued Job → worker execution → observable terminal Job. The production default registry remains free of Phase 19 operation handlers.

**Source:** CONTEXT D-01–D-05 and D-13; REQUIREMENTS.md ORCH-01–ORCH-04; RESEARCH.md Architecture Patterns.

---

## Copywriting Contract

No UI copy is authorized in this phase.

| Element | Copy |
|---------|------|
| Primary CTA | Not applicable — console trigger labels are Phase 19 scope. |
| Empty state heading | Not applicable — no browser state is introduced. |
| Empty state body | Not applicable — no browser state is introduced. |
| Error state | No visual error copy. HTTP clients receive the typed status/error semantics in the interaction contract above. Exact error-body field names beyond locked machine-code/original-Job-ID requirements remain implementation discretion. |
| Destructive confirmation | Not applicable — cancellation is an HTTP request; browser confirmation UX is Phase 19 scope. Cancellation never deletes Job or audit history. |

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none | not applicable — no frontend/component work |
| Third-party registries | none | not applicable — no third-party blocks declared |

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: N/A — backend-only; scope fence verified
- [ ] Dimension 2 Visuals: N/A — no rendered UI authorized
- [ ] Dimension 3 Color: N/A — no rendered UI authorized
- [ ] Dimension 4 Typography: N/A — no rendered UI authorized
- [ ] Dimension 5 Spacing: N/A — no rendered UI authorized
- [ ] Dimension 6 Registry Safety: N/A — no component registry used

**Approval:** pending checker verification
