COMPOSE ?= docker compose
STRATEGY ?= trend_following_daily
PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
PYTHONPATH_PREFIX ?= PYTHONPATH=src
FROM_DATE ?=
TO_DATE ?=
SYMBOLS ?=

# Canonical host development (`make dev`): the research product -- API + Job
# worker in research mode against the research database, plus the console --
# reloading on backend code changes. The frozen trading surface is reachable
# only through `make dev-trading` (same processes, trading mode, `.env` database).
UVICORN ?= .venv/bin/uvicorn
RESEARCH_DB ?= trading_research
RESEARCH_CALENDAR_START ?= 2014-01-02
# Applied to every research process (API, worker, bootstrap). The database name is
# forced here so no `.env` edit can point a research process at `trading_platform`.
RESEARCH_ENV ?= TRADING_PLATFORM_RESEARCH__MODE=true TRADING_PLATFORM_RESEARCH__CALENDAR_START=$(RESEARCH_CALENDAR_START) TRADING_PLATFORM_DATABASE__NAME=$(RESEARCH_DB)
WATCHFILES ?= .venv/bin/watchfiles
API_HOST ?= 127.0.0.1
API_PORT ?= 8000
CONSOLE_PORT ?= 3000
DEV_PORTS ?= $(API_PORT) $(CONSOLE_PORT)
API_CMD ?= $(PYTHONPATH_PREFIX) $(UVICORN) trading_platform.api.app:app --reload --reload-dir src --host $(API_HOST) --port $(API_PORT)
# SIGINT lets the worker finish its in-flight Job; after 30s it is killed and
# the lost-lease sweep reclaims the Job.
WORKER_CMD ?= $(PYTHONPATH_PREFIX) $(WATCHFILES) --filter python --sigint-timeout 30 '$(PYTHON) -m trading_platform.worker run-jobs' src
CONSOLE_CMD ?= cd console && npm run dev -- --port $(CONSOLE_PORT)

# The full container stack (migrate/api/worker) sits behind the `stack`
# profile; a bare `docker compose up` starts only the database.
STACK_COMPOSE = $(COMPOSE) --profile stack

.PHONY: up down logs migrate seed export-backtest-report generate-signals test console console-install api worker dev dev-trading research-bootstrap

up:
	$(STACK_COMPOSE) up --build -d

down:
	$(STACK_COMPOSE) down --remove-orphans

logs:
	$(STACK_COMPOSE) logs -f db api worker

api:
	$(RESEARCH_ENV) $(API_CMD)

worker:
	$(RESEARCH_ENV) $(WORKER_CMD)

# One-time (idempotent) preparation of the research database: create + migrate,
# seed the four example strategy versions, sync the public asset catalog and the
# exchange sessions from the pinned calendar start. Safe to re-run; never clears.
research-bootstrap:
	$(RESEARCH_ENV) $(PYTHONPATH_PREFIX) $(PYTHON) scripts/bootstrap_research.py

# Fails fast when any dev port is taken (e.g. by the Compose stack), then runs
# API, worker and console in one process group; Ctrl-C stops all of them.
# Mutations are enabled for this API process only; the app default stays off.
# `dev` is the research product; `dev-trading` the frozen trading console.
define DEV_PORT_CHECK
command -v lsof >/dev/null 2>&1 || { echo "make dev: lsof is required for the port check" >&2; exit 1; }; \
	busy=0; \
	for port in $(DEV_PORTS); do \
		if lsof -nP -iTCP:$$port -sTCP:LISTEN >/dev/null 2>&1; then \
			echo "make dev: port $$port is already in use:" >&2; \
			lsof -nP -iTCP:$$port -sTCP:LISTEN >&2; \
			busy=1; \
		fi; \
	done; \
	if [ $$busy -ne 0 ]; then \
		echo "make dev: stop the process above first (Compose stack: make down)." >&2; \
		exit 1; \
	fi
endef

dev:
	@$(DEV_PORT_CHECK)
	@trap 'trap - INT TERM EXIT; kill 0 2>/dev/null' INT TERM EXIT; \
	$(RESEARCH_ENV) TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true $(API_CMD) & \
	$(RESEARCH_ENV) $(WORKER_CMD) & \
	$(CONSOLE_CMD) & \
	wait

dev-trading:
	@$(DEV_PORT_CHECK)
	@trap 'trap - INT TERM EXIT; kill 0 2>/dev/null' INT TERM EXIT; \
	TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true $(API_CMD) & \
	$(WORKER_CMD) & \
	$(CONSOLE_CMD) & \
	wait

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
	$(CONSOLE_CMD)
