COMPOSE ?= docker compose
STRATEGY ?= trend_following_daily
PYTHONPATH_PREFIX ?= PYTHONPATH=src

.PHONY: up down logs migrate dry-run test

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down --remove-orphans

logs:
	$(COMPOSE) logs -f db api worker

migrate:
	@echo "Phase 1 Plan 01 scaffold complete. Database migrations land in Phase 1 Plan 02."

dry-run:
	$(PYTHONPATH_PREFIX) python3 -m trading_platform.worker dry-run --strategy $(STRATEGY)

test:
	$(PYTHONPATH_PREFIX) pytest tests/test_app_boot.py -q

