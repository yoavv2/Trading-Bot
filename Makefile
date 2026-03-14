COMPOSE ?= docker compose
STRATEGY ?= trend_following_daily
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
PYTHONPATH_PREFIX ?= PYTHONPATH=src
FROM_DATE ?=
TO_DATE ?=
SYMBOLS ?=

.PHONY: up down logs migrate seed dry-run ingest-bars sync-metadata sync-sessions test

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down --remove-orphans

logs:
	$(COMPOSE) logs -f db api worker

migrate:
	$(PYTHONPATH_PREFIX) $(PYTHON) scripts/migrate.py upgrade head

seed:
	$(PYTHONPATH_PREFIX) $(PYTHON) scripts/seed_phase1.py

dry-run:
	$(PYTHONPATH_PREFIX) $(PYTHON) -m trading_platform.worker dry-run --strategy $(STRATEGY)

ingest-bars:
	$(PYTHONPATH_PREFIX) $(PYTHON) scripts/ingest_polygon_bars.py \
		$(if $(FROM_DATE),--from-date $(FROM_DATE),) \
		$(if $(TO_DATE),--to-date $(TO_DATE),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),)

sync-metadata:
	$(PYTHONPATH_PREFIX) $(PYTHON) scripts/sync_symbol_metadata.py \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),)

sync-sessions:
	$(PYTHONPATH_PREFIX) $(PYTHON) -m trading_platform.worker sync-sessions \
		$(if $(FROM_DATE),--from-date $(FROM_DATE),) \
		$(if $(TO_DATE),--to-date $(TO_DATE),)

test:
	$(PYTHONPATH_PREFIX) $(PYTEST) tests/test_app_boot.py tests/test_db_migrations.py tests/test_strategy_registry.py tests/test_dry_run.py tests/test_market_data_ingestion.py tests/test_market_data_access.py -q
