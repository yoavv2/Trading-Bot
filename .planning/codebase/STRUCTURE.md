# Directory Structure

**Analysis Date:** 2026-04-16

## Root Layout

```
Trading Bot Project/
├── src/trading_platform/       # Main application package
│   ├── api/                    # FastAPI presentation layer
│   │   ├── app.py              # App factory (64 lines)
│   │   └── routes/             # 6 route modules
│   │       ├── health.py
│   │       ├── strategies.py
│   │       ├── runs.py
│   │       ├── analytics.py
│   │       ├── operations.py
│   │       └── system.py
│   ├── core/                   # Configuration & logging
│   │   ├── settings.py         # Typed settings (385 lines)
│   │   └── logging.py          # JSON structured logging
│   ├── db/                     # Data persistence layer
│   │   ├── session.py          # Session/engine management (93 lines)
│   │   └── models/             # 14 ORM models
│   │       ├── __init__.py     # Barrel file aggregating all models
│   │       ├── strategy.py
│   │       ├── strategy_run.py
│   │       ├── daily_bar.py
│   │       ├── market_session.py
│   │       ├── signal.py
│   │       ├── trade.py
│   │       ├── order.py
│   │       ├── fill.py
│   │       ├── position.py
│   │       ├── risk_event.py
│   │       └── symbol.py
│   ├── services/               # Business logic (18 modules)
│   │   ├── bootstrap.py
│   │   ├── backtest.py
│   │   ├── ingestion.py
│   │   ├── market_data_access.py
│   │   ├── risk.py
│   │   ├── paper_execution.py
│   │   ├── reconciliation.py
│   │   ├── analytics.py
│   │   ├── portfolio.py
│   │   ├── calendar.py
│   │   ├── polygon_client.py
│   │   ├── alpaca_client.py
│   │   └── ...
│   ├── strategies/             # Strategy domain
│   │   ├── base.py             # BaseStrategy ABC
│   │   ├── registry.py         # Strategy registry
│   │   ├── signals.py          # Signal dataclasses
│   │   └── trend_following_daily_v1/
│   │       └── strategy.py     # TrendFollowingDailyV1 implementation
│   └── worker/                 # CLI orchestration
│       └── __main__.py         # Command dispatch (673 lines)
├── alembic/                    # Database migrations
│   ├── env.py
│   └── versions/               # Migration scripts
├── config/                     # Configuration files
│   ├── app.yaml                # Main app config
│   └── strategies/             # Per-strategy YAML configs
│       └── trend_following_daily.yaml
├── tests/                      # Test suite
│   ├── test_app_boot.py
│   ├── test_backtest_runner.py
│   ├── test_db_migrations.py
│   ├── test_dry_run.py
│   ├── test_market_data_access.py
│   ├── test_market_data_ingestion.py
│   ├── test_strategy_registry.py
│   ├── test_trend_following_strategy.py
│   └── fixtures/               # Test data fixtures
├── .planning/                  # GSD planning artifacts
├── pyproject.toml              # Project config, dependencies
├── Makefile                    # Build/run commands
├── docker-compose.yml          # Local dev services
└── .env                        # Environment variables (not committed)
```

## Key Locations

| What | Where |
|------|-------|
| App factory | `src/trading_platform/api/app.py` |
| Settings | `src/trading_platform/core/settings.py` |
| DB session | `src/trading_platform/db/session.py` |
| ORM models | `src/trading_platform/db/models/` |
| Services | `src/trading_platform/services/` |
| Strategy ABC | `src/trading_platform/strategies/base.py` |
| Strategy registry | `src/trading_platform/strategies/registry.py` |
| Worker CLI | `src/trading_platform/worker/__main__.py` |
| Migrations | `alembic/versions/` |
| App config | `config/app.yaml` |
| Strategy configs | `config/strategies/` |
| Tests | `tests/test_*.py` |

## Naming Conventions

- **Files**: `snake_case.py` (e.g., `strategy_run.py`, `risk_event.py`)
- **Classes**: PascalCase for models, services, ABCs (e.g., `StrategyRun`, `RiskService`)
- **Functions**: snake_case (e.g., `run_backtest()`, `session_scope()`)
- **ORM Models**: Singular class name, plural table name (e.g., `Strategy` -> `strategies` table)
- **Config files**: snake_case YAML (e.g., `trend_following_daily.yaml`)

## Where to Add New Code

| Adding | Location |
|--------|----------|
| New API endpoint | `src/trading_platform/api/routes/` |
| New strategy | Extend `BaseStrategy` in `src/trading_platform/strategies/` |
| New service | `src/trading_platform/services/` with dataclass contracts |
| New ORM model | `src/trading_platform/db/models/` + Alembic migration |
| New worker command | `src/trading_platform/worker/__main__.py` |
| General utilities | `src/trading_platform/core/` |

---

*Structure analysis: 2026-04-16*
