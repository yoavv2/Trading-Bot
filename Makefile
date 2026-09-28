COMPOSE ?= docker compose
STRATEGY ?= trend_following_daily
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
PYTHONPATH_PREFIX ?= PYTHONPATH=src
FROM_DATE ?=
TO_DATE ?=
SYMBOLS ?=

.PHONY: up down logs migrate seed export-backtest-report generate-signals test console console-install

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

export-backtest-report:
	$(PYTHONPATH_PREFIX) $(PYTHON) scripts/export_backtest_report.py \
		$(if $(RUN_ID),--run-id $(RUN_ID),--strategy $(STRATEGY)) \
		--summary-format $(or $(SUMMARY_FORMAT),markdown) \
		$(if $(OUTPUT_DIR),--output-dir $(OUTPUT_DIR),)

generate-signals:
	$(PYTHONPATH_PREFIX) $(PYTHON) scripts/generate_signals.py \
		--strategy $(STRATEGY) \
		$(if $(AS_OF),--as-of $(AS_OF),)

test:
	$(PYTHONPATH_PREFIX) $(PYTEST) tests/test_app_boot.py tests/test_db_migrations.py tests/test_strategy_registry.py tests/test_market_data_ingestion.py tests/test_market_data_access.py tests/test_trend_following_strategy.py -q

console-install:
	cd console && npm install

console:
	cd console && npm run dev
