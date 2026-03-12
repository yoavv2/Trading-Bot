# Phase 1: Foundation Platform - Context

**Gathered:** 2026-03-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Stand up a local-first Python platform skeleton with real persistence, clean module boundaries, and a working dry execution path for a registered strategy. This phase proves that the system is a real extensible platform rather than a script scaffold.

Phase 1 is done when the stack boots locally, the database migrates cleanly, the FastAPI health surface responds, a strategy can be resolved from the registry, a dry platform run can execute without market or broker integrations, that run is persisted, and structured logs plus database state prove the core contracts are real.

</domain>

<decisions>
## Implementation Decisions

### Phase 1 proof point
- The most important outcome is not feature breadth but proof of platform reality: boot the platform locally, verify persistence and service boundaries, and run a dry strategy bootstrap path end to end.
- The dry path must do more than return success in memory. It must persist at least a minimal `strategy_run` or equivalent bootstrap record.
- Structured logs for startup and dry-run execution are part of the proof, not optional polish.

### Operating model
- Phase 1 should use a mixed control surface: CLI or scripts for operator workflows, FastAPI for service boundaries.
- Scripts should cover local bootstrapping, migrations, minimal seeding, dry strategy execution, and developer setup tasks.
- FastAPI should stay intentionally thin in this phase: `GET /health`, `GET /ready`, and a minimal versioned platform surface. `GET /strategies` is appropriate if it helps prove the registry boundary.
- The CLI remains the primary operator surface in Phase 1. The API exists to establish service boundaries, not to become the main product interface yet.

### Configuration model
- Lock Phase 1 to file-first configuration with environment variables for secrets.
- Use checked-in config files for non-secret runtime and application configuration.
- Use `.env` for secrets and machine-specific values.
- Use a typed Python settings loader with explicit schema validation.
- Strategy configuration objects should already use a shape that could later live in the database, but the database is not the source of truth in Phase 1.
- Remote config management, admin editing flows, and dynamic DB-backed config are explicitly deferred.

### Architecture boundaries
- The highest-priority quality bar for Phase 1 is explicit, enforced boundaries between:
  - strategy
  - data
  - portfolio
  - risk
  - execution
  - analytics
  - persistence
  - API
  - infra/config/logging
- Some of these may be placeholders in Phase 1, but the contracts must already be real and clean.
- The platform should clearly read as a strategy platform from its module structure and interfaces, not as an app that will later need to be reorganized.

### Persistence and schema scope
- Real persistence and migration flow are mandatory in Phase 1.
- By the end of this phase there should be working SQLAlchemy or SQLModel models, Alembic setup, an initial migration, and a clean migration command flow.
- Minimal seed or bootstrap records are required so the system proves it is not static scaffolding.
- Keep the initial schema intentionally small. Include `strategies`, `strategy_runs`, and optionally `app_events` or `audit_events`.
- Defer large trading tables such as `market_bars`, `signals`, `orders`, `fills`, `positions`, `risk_events`, and `account_snapshots` unless one is strictly required to support the dry-run proof.

### Strategy extensibility proof
- Strategy extensibility must be real in Phase 1, not aspirational.
- Include a base strategy interface, a registry, strategy metadata, and a dry-run bootstrap path that proves strategy modules can be discovered and executed.
- The dry execution path should load config, resolve the strategy from the registry, create a dry `strategy_run`, persist run status, and log the execution result.
- The Phase 1 strategy path is an empty or mock execution path only. No market data, broker calls, signal generation, or backtest logic belongs here.

### Developer ergonomics
- Local development should feel good enough that the project can grow without friction.
- Preferred control surface is a short command set such as:
  - `make up`
  - `make down`
  - `make migrate`
  - `make seed`
  - `make dry-run STRATEGY=trend_following_daily`
- One command or one short sequence should boot the local stack.
- One command should apply migrations.
- One command should execute the dry strategy bootstrap flow.
- Logs should be clear and environment setup should be predictable.

### Deliverable shape
- Infrastructure deliverables should include Docker Compose for app and Postgres, optional local volumes, `.env.example`, typed settings loading, and structured logging.
- Backend deliverables should include a FastAPI app skeleton, `/health`, `/ready`, and a basic API version route.
- Persistence deliverables should include DB session management, initial domain models, Alembic setup, the initial migration, and the migration command flow.
- Core contract deliverables should include the base strategy interface, strategy registry, strategy metadata model, and placeholder service interfaces for market data, broker, risk, and analytics.
- Testing should stay minimal but real: app boot test, DB connection test, migration smoke test, strategy registry test, and dry-run persistence test.

### Priority order
- Priority 1: architecture boundaries
- Priority 2: persistence and migrations
- Priority 3: future-strategy extensibility
- Priority 4: local developer ergonomics

### Claude's Discretion
- Exact package layout beneath the agreed module boundaries
- Choice between SQLAlchemy and SQLModel, as long as migrations and models stay clean
- Task runner details beyond the required operator commands
- Whether `GET /strategies` or a dry-run bootstrap endpoint belongs in this phase, as long as the API surface stays minimal
- Whether to include `app_events` or `audit_events` in the initial schema, as long as auditability for startup and dry runs remains credible

</decisions>

<specifics>
## Specific Ideas

- The correct Phase 1 outcome is: start the local stack, connect to Postgres, apply migrations, expose a minimal FastAPI service, inspect a real schema, register a strategy, execute a dry bootstrap path, persist that run, and emit structured logs.
- The phase should prove "real and extensible platform" rather than "business logic exists."
- The correct operating split is: CLI or scripts for operator control, FastAPI for platform boundary.
- Suggested configuration split:
  - `config/app.yaml`
  - `config/strategies/trend_following_daily.yaml`
  - `.env`
- Suggested minimal persisted entities:
  - `strategies`
  - `strategy_runs`
  - optional `app_events` or `audit_events`
- Suggested minimal API surface:
  - `GET /health`
  - `GET /ready`
  - `GET /strategies`
  - optional `POST /bootstrap/dry-run/{strategy}` if it helps prove the boundary later in the phase

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- None yet. The repository does not contain application code beyond planning artifacts.

### Established Patterns
- None yet. Phase 1 is responsible for establishing the first durable platform patterns.

### Integration Points
- The first integration points will be the FastAPI app entrypoint, the worker or script entrypoints, the database session and migration stack, and the strategy registry bootstrap flow.

</code_context>

<deferred>
## Deferred Ideas

- Real Polygon ingestion
- Real Alpaca integration
- Runnable historical backtests
- Signal generation with market logic
- Order submission
- Fill handling
- Portfolio math beyond domain placeholders
- Real risk rules beyond contracts or placeholders
- Analytics calculations beyond skeletal structures
- Scheduled daily automation
- Reconciliation logic
- Websocket support
- Dashboard or frontend work
- Node.js application work
- Authentication
- Multi-user support
- Cloud deployment
- Redis-dependent workflows unless absolutely needed for bootstrap
- Live-trading concerns
- Performance optimization
- Advanced simulation testing

</deferred>

---

*Phase: 01-foundation-platform*
*Context gathered: 2026-03-12*
