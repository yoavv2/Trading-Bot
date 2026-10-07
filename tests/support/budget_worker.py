"""Spawned-process worker for the shared-budget tests: a separate OS process with its own
engine and its own ``DatabaseRequestBudget`` instance tries ``attempts`` admissions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)


def admit_many(database_name: str, attempts: int, limit: int, tag: str) -> dict[str, int]:
    os.environ["TRADING_PLATFORM_DATABASE__NAME"] = database_name
    from trading_platform.core import settings as settings_module
    from trading_platform.services.research.budget import (
        BudgetExhaustedError,
        BudgetLimits,
        DatabaseRequestBudget,
    )

    settings_module.EnvironmentOverrides.model_config["env_file"] = None
    settings_module.clear_settings_cache()
    settings = settings_module.load_settings()
    budget = DatabaseRequestBudget(
        settings,
        provider="tiingo",
        limits=BudgetLimits(requests_per_hour=limit, requests_per_day=10_000, unique_symbols_per_month=10_000),
        process_id=f"worker-{tag}-{os.getpid()}",
    )
    admitted = refused = 0
    for index in range(attempts):
        try:
            admission = budget.admit(symbol=f"SYM{index % 3}", purpose="prices")
            budget.complete(admission, outcome="ok", status_code=200)
            admitted += 1
        except BudgetExhaustedError:
            refused += 1
    return {"admitted": admitted, "refused": refused, "pid": os.getpid()}
