# Phase 1: Foundation Platform - Research

**Researched:** 2026-03-12
**Domain:** Python service foundation for a local-first algorithmic trading platform
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- The most important outcome is not feature breadth but proof of platform reality: boot the platform locally, verify persistence and service boundaries, and run a dry strategy bootstrap path end to end.
- The dry path must do more than return success in memory. It must persist at least a minimal `strategy_run` or equivalent bootstrap record.
- Structured logs for startup and dry-run execution are part of the proof, not optional polish.
- Phase 1 should use a mixed control surface: CLI or scripts for operator workflows, FastAPI for service boundaries.
- Scripts should cover local bootstrapping, migrations, minimal seeding, dry strategy execution, and developer setup tasks.
- FastAPI should stay intentionally thin in this phase: `GET /health`, `GET /ready`, and a minimal versioned platform surface. `GET /strategies` is appropriate if it helps prove the registry boundary.
- The CLI remains the primary operator surface in Phase 1. The API exists to establish service boundaries, not to become the main product interface yet.
- Lock Phase 1 to file-first configuration with environment variables for secrets.
- Use checked-in config files for non-secret runtime and application configuration.
- Use `.env` for secrets and machine-specific values.
- Use a typed Python settings loader with explicit schema validation.
- Strategy configuration objects should already use a shape that could later live in the database, but the database is not the source of truth in Phase 1.
- Remote config management, admin editing flows, and dynamic DB-backed config are explicitly deferred.
- The highest-priority quality bar for Phase 1 is explicit, enforced boundaries between strategy, data, portfolio, risk, execution, analytics, persistence, API, and infra/config/logging.
- Some of these may be placeholders in Phase 1, but the contracts must already be real and clean.
- Real persistence and migration flow are mandatory in Phase 1.
- Keep the initial schema intentionally small. Include `strategies`, `strategy_runs`, and optionally `app_events` or `audit_events`.
- Defer large trading tables such as `market_bars`, `signals`, `orders`, `fills`, `positions`, `risk_events`, and `account_snapshots` unless one is strictly required to support the dry-run proof.
- Strategy extensibility must be real in Phase 1, not aspirational.
- Include a base strategy interface, a registry, strategy metadata, and a dry-run bootstrap path that proves strategy modules can be discovered and executed.
- The dry execution path should load config, resolve the strategy from the registry, create a dry `strategy_run`, persist run status, and log the execution result.
- The Phase 1 strategy path is an empty or mock execution path only. No market data, broker calls, signal generation, or backtest logic belongs here.
- Preferred control surface is a short command set such as `make up`, `make down`, `make migrate`, `make seed`, and `make dry-run STRATEGY=trend_following_daily`.
- Testing should stay minimal but real: app boot test, DB connection test, migration smoke test, strategy registry test, and dry-run persistence test.

### Claude's Discretion
- Exact package layout beneath the agreed module boundaries
- Choice between SQLAlchemy and SQLModel, as long as migrations and models stay clean
- Task runner details beyond the required operator commands
- Whether `GET /strategies` or a dry-run bootstrap endpoint belongs in this phase, as long as the API surface stays minimal
- Whether to include `app_events` or `audit_events` in the initial schema, as long as auditability for startup and dry runs remains credible

### Deferred Ideas (OUT OF SCOPE)
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
</user_constraints>

<research_summary>
## Summary

Phase 1 does not need exotic ecosystem research, but it does benefit from locking the current standard Python service foundation before planning. The relevant official docs all point toward the same low-risk stack: FastAPI with the `lifespan` startup model, SQLAlchemy 2.x with typed declarative mappings, Alembic for migrations, `psycopg` 3 for PostgreSQL, `pydantic-settings` for typed configuration, Docker Compose healthchecks for dependency readiness, and `pytest` with a `src/` layout for smoke coverage. This is a mature path with strong docs and minimal surprise for a local-first service that has both CLI and API entrypoints.

For this phase, the key architectural choice is to stay boring and explicit. Use synchronous database access, a standard Alembic setup, file-first config with environment overrides, and a deliberately small schema centered on `strategies` and `strategy_runs`. That best matches the locked Phase 1 objective: prove that the platform skeleton is real, extensible, and observable without pulling future broker, market-data, or analytics complexity into the foundation layer.

The only meaningful tension is that the roadmap's original Phase 1 success criteria mention a much broader persistence footprint than the Phase 1 context now allows. The research recommendation is to treat the context as authoritative for planning: build only the minimal schema needed to prove the platform contracts, and defer the rest of the trading tables to later phases.

**Primary recommendation:** Build Phase 1 on FastAPI + SQLAlchemy 2.x + Alembic + `psycopg` 3 + `pydantic-settings`, keep the schema minimal, keep the runtime synchronous, and use the dry strategy bootstrap path as the single proof that the platform boundaries are real.
</research_summary>

<standard_stack>
## Standard Stack

The established libraries and tools for this phase:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.131.x | Thin API boundary, dependency injection, health/readiness endpoints | Current docs emphasize `lifespan` and larger-app structure; it remains the default Python choice for typed service APIs |
| SQLAlchemy | 2.0.x | ORM, sessions, metadata, typed models | Official 2.x docs center on `DeclarativeBase`, `Mapped`, and `mapped_column`, which fit explicit boundaries and Alembic well |
| Alembic | 1.18.x | Schema migrations | The standard migration tool for SQLAlchemy-backed services; avoids hand-rolled schema drift |
| psycopg | 3.x | PostgreSQL driver | Modern PostgreSQL driver for Python; SQLAlchemy 2.1 migrations note the PostgreSQL default dialect moved to `psycopg` |
| pydantic-settings | 2.12.x | Typed settings and env loading | Pydantic v2 moved `BaseSettings` here; it supports `.env` files and custom source ordering |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Uvicorn | current stable | ASGI server for local API runtime | Use for local app startup and container entrypoint |
| PyYAML | 6.x | Load checked-in YAML config files | Use for file-first app and strategy config before DB-backed config exists |
| Docker Compose | Compose Spec / current stable | Local orchestration for app + Postgres | Use for predictable local boot and service health gating |
| pytest | 9.x | Smoke and registry/persistence tests | Use for minimal but real verification of boot, migrations, and dry runs |
| Python `logging` stdlib | Python 3.12 | Structured logging baseline | Start here before adding heavier logging frameworks |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| SQLAlchemy + Alembic | SQLModel | SQLModel is viable and current, but SQLAlchemy 2.x gives clearer control over models, sessions, and Alembic behavior for a persistence-first foundation phase |
| Sync SQLAlchemy | Async SQLAlchemy | Async may help later for higher I/O concurrency, but it adds complexity across CLI scripts, tests, and Alembic for little Phase 1 payoff |
| Standard Alembic init (`alembic.ini` + `env.py`) | `pyproject.toml` templates | Alembic supports pyproject templates now, but standard init remains simpler and better documented for a first migration setup |
| stdlib logging + JSON formatter | `structlog` or `loguru` | Those libraries can improve ergonomics later, but the stdlib is enough to establish structured logs now |

**Installation:**
```bash
pip install fastapi uvicorn[standard] sqlalchemy alembic "psycopg[binary]" pydantic-settings PyYAML pytest
```
</standard_stack>

<architecture_patterns>
## Architecture Patterns

### Recommended Project Structure
```text
config/
  app.yaml
  strategies/
    trend_following_daily.yaml
src/
  trading_platform/
    api/
      app.py
      routes/
    core/
      settings.py
      logging.py
    db/
      base.py
      session.py
      models/
    strategies/
      base.py
      registry.py
      trend_following_daily/
    services/
      bootstrap.py
      data.py
      risk.py
      execution.py
      analytics.py
scripts/
tests/
alembic/
  versions/
```

### Pattern 1: Lifespan-Based Service Composition
**What:** Initialize settings, logging, database engine, and lightweight shared resources in a FastAPI `lifespan` context instead of scattering boot logic across decorators or module import side effects.
**When to use:** Always for this service. It keeps startup/shutdown deterministic for both local development and container runtime.
**Example:**
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = load_settings()
    app.state.engine = build_engine(app.state.settings.database_url)
    configure_logging(app.state.settings)
    yield
    app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)
```

### Pattern 2: Typed ORM Base with Central Metadata
**What:** Define a single declarative base and metadata object, including naming conventions, then keep models in a dedicated persistence package.
**When to use:** From the first migration onward. It keeps Alembic diffs stable and preserves a clear persistence boundary.
**Example:**
```python
from sqlalchemy import MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    metadata = metadata


class Strategy(Base):
    __tablename__ = "strategies"

    name: Mapped[str] = mapped_column(String(100), unique=True)
```

### Pattern 3: File-First Typed Settings with Source Layering
**What:** Load checked-in app and strategy config from files, overlay environment variables for secrets and machine-specific values, and validate the merged result through typed Pydantic models.
**When to use:** Throughout Phase 1 and likely Phase 2. This matches the locked file-first config decision while keeping the shape ready for future DB-backed config.
**Example:**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    log_level: str = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            yaml_settings_source(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )
```

### Pattern 4: Compose-Gated Readiness
**What:** Distinguish "the process is up" from "dependencies are ready" and use Compose healthchecks plus a readiness endpoint to enforce that distinction.
**When to use:** From the first Docker Compose setup. It prevents flaky local boot and gives the API a meaningful `GET /ready`.
**Example:**
```yaml
services:
  db:
    image: postgres:17
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    depends_on:
      db:
        condition: service_healthy
```

### Anti-Patterns to Avoid
- **Import-time side effects:** Do not create engines, sessions, or registry state at module import time. Use explicit bootstrap paths instead.
- **Async by default:** Do not introduce async database plumbing until there is a concrete I/O need that outweighs the complexity cost.
- **Schema inflation:** Do not create every future trading table in Phase 1 just because the long-term platform will need them.
- **Untyped config blobs:** Do not let strategy or app config live as raw dictionaries without schema validation.
- **API-driven operations only:** Do not force migrations or dry strategy runs through HTTP. CLI and scripts remain the primary operator surface in this phase.
</architecture_patterns>

<dont_hand_roll>
## Don't Hand-Roll

Problems that look simple but already have established solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Schema migration workflow | Custom SQL files and ad hoc migration scripts | Alembic | Migration ordering, downgrade handling, metadata diffing, and team repeatability are already solved here |
| ORM, sessions, and PostgreSQL dialect behavior | Thin homegrown DB wrapper | SQLAlchemy + `psycopg` | You want predictable transactions, typed models, and first-class Alembic integration |
| Settings precedence and env parsing | Manual YAML plus `os.environ` merge logic | `pydantic-settings` + Pydantic models | Validation, `.env` support, aliases, and custom source ordering are already built in |
| Container dependency coordination | Sleep loops or retry shell scripts | Docker Compose healthchecks and `service_healthy` | Startup-order edge cases are common and already covered by Compose patterns |
| Logging scaffolding | `print()` plus custom JSON string assembly | Python `logging` with structured formatting | Centralized handlers, levels, context propagation, and predictable output are already solved |

**Key insight:** Hand-roll the trading-domain contracts, registry, and dry-run orchestration. Do not hand-roll migrations, configuration precedence, connection plumbing, or container readiness.
</dont_hand_roll>

<common_pitfalls>
## Common Pitfalls

### Pitfall 1: Using outdated FastAPI startup patterns
**What goes wrong:** Startup logic gets split between decorators, imports, and tests, which makes readiness and shutdown behavior harder to reason about.
**Why it happens:** Older examples use `startup` and `shutdown` events, and they are still widespread in blog posts.
**How to avoid:** Use a single `lifespan` function as the canonical bootstrap/shutdown path and keep initialization there.
**Warning signs:** Tests need import hacks, shutdown leaks connections, or local boot behaves differently from container boot.

### Pitfall 2: Choosing async database plumbing before it is needed
**What goes wrong:** CLI scripts, migrations, and tests become harder to compose, and the team spends time on event-loop details instead of proving platform boundaries.
**Why it happens:** Modern Python service examples often default to async stacks even when the workload is simple and scheduled.
**How to avoid:** Keep database access synchronous in Phase 1. Revisit async only when there is a concrete concurrency bottleneck or real async integration requirement.
**Warning signs:** Simple scripts need event-loop wrappers, database utilities bifurcate into sync and async versions, or tests become awkward around loop management.

### Pitfall 3: Letting the Phase 1 schema grow to fit the full roadmap
**What goes wrong:** Phase 1 starts implementing later trading concepts before the platform skeleton has proved its core contracts.
**Why it happens:** The roadmap language is broader than the locked context and can pull planning toward premature table design.
**How to avoid:** Keep the persisted proof small: `strategies`, `strategy_runs`, and optional `app_events` or `audit_events`.
**Warning signs:** Migrations introduce bars, orders, fills, or positions before there is any real data, execution, or backtest logic using them.

### Pitfall 4: Blurring health and readiness
**What goes wrong:** `/health` reports success even when Postgres or config loading is broken, so the system looks booted when it is not actually usable.
**Why it happens:** Teams often implement a single heartbeat endpoint and skip dependency checks.
**How to avoid:** Make `/health` process-level and `/ready` dependency-aware, including at least config load and database connectivity.
**Warning signs:** The API container responds, but migrations fail, dry runs fail immediately, or Compose starts dependent services before Postgres is usable.

### Pitfall 5: Treating config files as unvalidated documents
**What goes wrong:** Subtle config errors only surface during runtime, or strategy modules silently accept malformed values.
**Why it happens:** YAML feels easy to edit, so teams skip typed validation and schema checks.
**How to avoid:** Parse files into typed Pydantic models early in the bootstrap path and fail fast on invalid config.
**Warning signs:** Unexpected `KeyError` or `TypeError` during dry runs, or strategy behavior changes because a config file drifted unnoticed.
</common_pitfalls>

<code_examples>
## Code Examples

Verified patterns from official sources:

### FastAPI lifespan bootstrap
```python
# Pattern aligned with:
# https://fastapi.tiangolo.com/advanced/events/
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = load_settings()
    app.state.engine = build_engine(app.state.settings.database_url)
    yield
    app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)
```

### Typed SQLAlchemy declarative mapping
```python
# Pattern aligned with:
# https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
```

### Custom settings source ordering
```python
# Pattern aligned with:
# https://docs.pydantic.dev/latest/concepts/pydantic_settings/
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            yaml_settings_source(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )
```

### Compose healthcheck dependency gating
```yaml
# Pattern aligned with:
# https://docs.docker.com/compose/how-tos/startup-order/
services:
  db:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]

  api:
    depends_on:
      db:
        condition: service_healthy
```
</code_examples>

<sota_updates>
## State of the Art (2024-2026)

What's changed recently:

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| FastAPI `startup` / `shutdown` decorators as the default startup pattern | FastAPI `lifespan` as the primary pattern, with event docs marked as deprecated alternative | FastAPI docs reflect this by 2026 | New service boot code should center on `lifespan` |
| `BaseSettings` imported from `pydantic` | `BaseSettings` moved to `pydantic-settings` | Pydantic v2 | New projects need a separate dependency and import path |
| SQLAlchemy 1.x style declarative examples | SQLAlchemy 2.x typed declarative with `DeclarativeBase`, `Mapped`, and `mapped_column` | SQLAlchemy 2.x | Cleaner typing and more consistent modern ORM code |
| `psycopg2` implicitly assumed as the PostgreSQL path | SQLAlchemy 2.1 changed the default PostgreSQL dialect to `psycopg` | SQLAlchemy 2.1 | New PostgreSQL projects should target `psycopg` 3 unless they have a legacy constraint |
| Alembic configured only through classic templates | Alembic 1.16+ added `pyproject` templates | Alembic 1.16+ | Useful option later, but not enough benefit to justify extra Phase 1 variation |

**New tools or patterns to consider:**
- `pydantic-settings` custom source ordering: good fit for file-first config with env override.
- FastAPI larger-app router structure: useful immediately because the project is intentionally platform-shaped, not a single-file app.
- SQLAlchemy metadata naming conventions: worth doing from migration one so constraint names stay stable.

**Deprecated or outdated:**
- Importing `BaseSettings` from `pydantic`
- Building new FastAPI initialization around `startup`/`shutdown` decorators
- Assuming PostgreSQL projects should default to `psycopg2`
</sota_updates>

<open_questions>
## Open Questions

1. **Should the planner revise the roadmap wording for Phase 1 schema scope?**
   - What we know: The locked Phase 1 context explicitly narrows the initial schema to `strategies`, `strategy_runs`, and optional audit-style tables.
   - What's unclear: Whether the roadmap itself should be updated before or during planning, because it still describes a much broader Phase 1 persistence footprint.
   - Recommendation: Treat the context as authoritative during planning. If the mismatch creates confusion, patch the roadmap as part of Phase 1 planning rather than broadening implementation.

2. **Do we need a persisted `app_events` or `audit_events` table in addition to `strategy_runs`?**
   - What we know: Structured logs are mandatory, and the dry-run proof must be auditable.
   - What's unclear: Whether startup and dry-run observability are sufficiently covered by logs plus `strategy_runs`, or whether a lightweight events table materially improves operator inspection in Phase 1.
   - Recommendation: Keep this optional. Add an events table only if it directly improves the dry-run proof without dragging later analytics concepts into Phase 1.

3. **Should any API write surface exist in Phase 1?**
   - What we know: The API must expose health and readiness, and `GET /strategies` is acceptable if it helps prove the registry boundary. The CLI remains the primary operator surface.
   - What's unclear: Whether a `POST /bootstrap/dry-run/{strategy}` endpoint adds useful proof in Phase 1 or just duplicates script functionality.
   - Recommendation: Do not make this mandatory. Prefer CLI-driven dry runs first; add a thin HTTP trigger only if it materially helps integration testing or operator inspection.
</open_questions>

<sources>
## Sources

### Primary (HIGH confidence)
- [FastAPI advanced events](https://fastapi.tiangolo.com/advanced/events/) - checked the current `lifespan` startup pattern and the deprecated status of alternative event hooks
- [FastAPI bigger applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) - checked package and router composition patterns for larger services
- [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/) - checked the current stable release line
- [Pydantic settings concepts](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) - checked `BaseSettings`, `.env` support, and `settings_customise_sources`
- [Pydantic migration guide](https://docs.pydantic.dev/latest/migration/#basesettings-has-moved-to-pydantic-settings) - verified `BaseSettings` moved out of the core package in v2
- [SQLAlchemy declarative tables](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html) - checked typed declarative mapping patterns
- [SQLAlchemy metadata naming conventions](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData.params.naming_convention) - checked recommended metadata naming conventions for stable migrations
- [SQLAlchemy 2.1 migration notes](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html) - checked the PostgreSQL default dialect change to `psycopg`
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html) - checked standard migration initialization and configuration flow
- [Alembic cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) - checked current config options and practical migration guidance
- [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/) - checked healthcheck-based dependency coordination
- [Python logging cookbook](https://docs.python.org/3/howto/logging-cookbook.html) - checked standard logging patterns suitable for structured logging setup
- [pytest good integration practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html) - checked `src/` layout and modern test organization guidance
- [SQLModel release notes](https://sqlmodel.tiangolo.com/release-notes/) - checked current SQLModel maturity and release line as an alternative
- [Psycopg 3 docs](https://www.psycopg.org/psycopg3/docs/) - checked modern PostgreSQL driver documentation for Python

### Secondary (MEDIUM confidence)
- None needed beyond official documentation for this phase

### Tertiary (LOW confidence - needs validation)
- None
</sources>

<metadata>
## Metadata

**Research scope:**
- Core technology: Python local-first service foundation
- Ecosystem: FastAPI, SQLAlchemy, Alembic, `psycopg`, `pydantic-settings`, Docker Compose, pytest
- Patterns: service startup, typed settings, migration discipline, schema boundaries, local operator ergonomics
- Pitfalls: async overreach, schema sprawl, weak readiness checks, untyped config

**Confidence breakdown:**
- Standard stack: HIGH - based on current official docs for mature and widely used libraries
- Architecture: HIGH - the recommended patterns match both official docs and the phase's locked operator constraints
- Pitfalls: HIGH - the main failure modes are common and directly implied by the chosen stack and phase scope
- Code examples: HIGH - all examples are adapted from current official documentation patterns

**Research date:** 2026-03-12
**Valid until:** 2026-04-11
</metadata>

---

*Phase: 01-foundation-platform*
*Research completed: 2026-03-12*
*Ready for planning: yes*
