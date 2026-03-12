COMPOSE ?= docker compose
STRATEGY ?= trend_following_daily
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
PYTHONPATH_PREFIX ?= PYTHONPATH=src

.PHONY: up down logs migrate seed dry-run test

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

test:
	$(PYTHONPATH_PREFIX) $(PYTEST) tests/test_app_boot.py tests/test_db_migrations.py tests/test_strategy_registry.py tests/test_dry_run.py -q
