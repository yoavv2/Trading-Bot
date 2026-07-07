# Coding Conventions

**Analysis Date:** 2026-07-07

## Naming Patterns

**Files:**
- Snake_case: `settings.py`, `ingestion.py`, `trading_platform/`
- Model files: `{entity}.py` (e.g., `paper_order.py`, `strategy_run.py`)
- Test files: `test_{module}.py` (e.g., `test_market_data_ingestion.py`)

**Functions:**
- Snake_case for all functions and methods
- Private helpers prefixed with underscore: `_load_yaml_file()`, `_make_polygon_settings()`
- Descriptive names indicating purpose: `build_settings_payload()`, `emit_structured_log()`

**Classes:**
- PascalCase: `BaseStrategy`, `PolygonClient`, `JsonLogFormatter`, `PaperOrder`
- Exception classes: `UnknownStrategyError`, `PolygonAuthError`, `IllegalOrderTransition`
- Model classes inherit from `TimestampedModel` and `Base`

**Variables:**
- Snake_case throughout
- Constants in UPPER_SNAKE_CASE: `_PROVIDER = "polygon"`, `PROJECT_ROOT`
- Private module variables prefixed with underscore: `_strategies`, `logger`

**Types:**
- PascalCase for types and enums: `OrderLifecycleState`, `StrategyRunStatus`
- Generic types with full type hints: `dict[str, Any]`, `list[str]`, `tuple[str, ...]`

## Code Style

**Formatting:**
- No explicit formatter configured (no .prettierrc, black, ruff config found)
- Consistent 4-space indentation (Python default)
- Line length appears to be ~100-120 characters based on observed code

**Linting:**
- No ESLint or black configuration in project root
- Uses `from __future__ import annotations` universally at top of modules
- PEP 8 style observed throughout

**Future Imports:**
All Python files start with:
```python
from __future__ import annotations
```
This enables postponed evaluation of type hints.

## Import Organization

**Order:**
1. Future imports: `from __future__ import annotations`
2. Standard library: `import json`, `from datetime import UTC, date`
3. Third-party: `from sqlalchemy import ...`, `from fastapi import ...`, `import pytest`
4. Local imports: `from trading_platform.core.settings import ...`
5. TYPE_CHECKING block for circular imports: Only import types inside `if TYPE_CHECKING:`

**Path Aliases:**
No path aliases configured. All imports are absolute from `trading_platform` root:
- `from trading_platform.core.settings import Settings`
- `from trading_platform.db.models.paper_order import PaperOrder`

**Example from `src/trading_platform/services/ingestion.py`:**
```python
from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_platform.core.settings import MarketDataSettings
from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel
```

**TYPE_CHECKING Pattern (from `src/trading_platform/db/models/paper_order.py`):**
```python
if TYPE_CHECKING:
    from trading_platform.db.models.execution_event import ExecutionEvent
    from trading_platform.db.models.paper_fill import PaperFill
```

## Error Handling

**Custom Exception Classes:**
- Frozen dataclasses that inherit from built-in exceptions
- Custom `__str__()` method for readable error messages

**Example from `src/trading_platform/strategies/registry.py`:**
```python
@dataclass(frozen=True)
class UnknownStrategyError(KeyError):
    strategy_id: str

    def __str__(self) -> str:
        return f"Unknown strategy '{self.strategy_id}'."
```

**Exception Chaining:**
Always use `from exc` to preserve tracebacks:
```python
try:
    return self._strategies[strategy_id]
except KeyError as exc:
    raise UnknownStrategyError(strategy_id) from exc
```

**Structured Error Context:**
Exceptions include semantic fields that help with debugging (see `IllegalOrderTransition` with reason fields).

**Error Propagation:**
- Database access functions use `session_scope()` context manager
- Network errors from HTTP clients captured and wrapped with context
- Service functions return structured result types rather than bare exceptions where possible

## Logging

**Framework:** Python standard `logging` module

**JSON Formatting:**
Custom `JsonLogFormatter` in `src/trading_platform/core/logging.py` outputs structured JSON logs:
```python
{
    "timestamp": "2024-07-07T10:30:00+00:00",
    "level": "INFO",
    "logger": "trading_platform.services.ingestion",
    "message": "Ingestion completed",
    "context": {
        "strategy_id": "trend_following_daily",
        "run_id": "uuid-...",
        "symbol_count": 10
    }
}
```

**Logger Names:**
Always use `__name__` at module level:
```python
logger = logging.getLogger(__name__)
```

**Structured Log Emission:**
Use `emit_structured_log()` helper for consistent context:
```python
emit_structured_log(
    logger,
    logging.INFO,
    "order_submitted",
    strategy_id="trend_following_daily",
    run_id=str(run_id),
    order_id=str(order_id),
)
```

**Context Building:**
Use `build_log_context()` to assemble log metadata. It filters out None values:
```python
context = build_log_context(
    strategy_id=strategy_id,
    run_id=run_id,
    session_date=session_date,
    blocked_reason=blocked_reason,  # Only included if not None
)
```

**When to Log:**
- Entry/exit of significant operations
- State transitions (order submitted, position opened)
- Error conditions with full context
- Performance milestones (ingestion start/end)

## Comments

**Module Docstrings:**
Every `.py` file has a module docstring:
```python
"""Typed runtime settings assembled from YAML files and environment overrides."""
```

**Class Docstrings:**
Classes have docstrings explaining purpose:
```python
class BaseStrategy(ABC):
    """Abstract strategy contract used by the registry and dry-run flow.

    Phase 2 extension: subclasses may implement ``generate_signals`` to
    emit typed ``SignalBatch`` output...
    """
```

**Function Docstrings:**
Functions have brief docstrings:
```python
def build_log_context(...) -> dict[str, Any]:
    """Assemble structured log context, filtering out None values."""
```

**Section Separators:**
Code sections are separated with visual dividers for readability:
```python
# ---------------------------------------------------------------------------
# Symbol upsert helpers
# ---------------------------------------------------------------------------

def _symbol_id_from_code(session: Session, symbol: str) -> uuid.UUID:
    ...
```

**Inline Comments:**
Minimal inline comments; code should be self-documenting through naming. Comments used for:
- Explaining non-obvious algorithm choices
- Referencing external specifications or bug reports
- Marking temporary workarounds with TODO/FIXME

## Function Design

**Size:**
Functions are generally small and focused (20-50 lines typical). Longer functions are orchestration functions that delegate to helpers.

**Parameters:**
- Type-hinted parameters with appropriate defaults
- Use keyword-only arguments (after `*`) for optional configuration:
```python
def build_settings_payload(
    *,
    config_file: Path | None = None,
    strategy_dir: Path | None = None,
) -> dict[str, Any]:
```

**Return Values:**
- Explicit return type annotations on all functions
- Functions return structured types (`@dataclass`, Pydantic models) rather than dicts when possible
- Functions that may fail return typed result types or raise custom exceptions

**Properties:**
- Use `@property` decorator for computed values:
```python
@property
def starting_cash_decimal(self) -> Decimal:
    return Decimal(str(self.starting_cash))
```

**Dataclasses:**
Used for immutable data structures with `frozen=True`:
```python
@dataclass(frozen=True)
class StrategyMetadata:
    strategy_id: str
    display_name: str
    # ...
```

## Module Design

**Exports:**
Explicit exports in `__init__.py` files:
```python
# src/trading_platform/db/models/__init__.py
from trading_platform.db.models.paper_order import PaperOrder
from trading_platform.db.models.strategy_run import StrategyRun
# ... other exports
```

**Private Implementation:**
Helper functions and internal classes are:
- Prefixed with underscore: `_load_yaml_file()`, `_deep_merge()`
- Placed in implementation modules, not exported

**Service Layer:**
Services are organized by responsibility:
- `src/trading_platform/services/ingestion.py` - Market data ingestion
- `src/trading_platform/services/execution.py` - Order execution
- `src/trading_platform/services/reconciliation.py` - State reconciliation

**Circular Import Avoidance:**
Uses `TYPE_CHECKING` blocks to import types only for static analysis:
```python
if TYPE_CHECKING:
    from trading_platform.db.models.strategy_run import StrategyRun
```
Runtime imports happen where needed via string type hints.

## Type Hints

**Consistent Usage:**
Type hints on all function parameters and return values. Examples:
```python
def load_settings(
    *,
    config_file: Path | None = None,
    strategy_dir: Path | None = None,
) -> Settings:
```

**Union Types:**
Use `|` operator (PEP 604) rather than `Union[X, Y]`:
```python
broker_order_id: str | None
response: dict[str, Any] | None
```

**Generic Collections:**
Lowercase generic syntax (PEP 585, Python 3.9+):
```python
dict[str, Any]
list[str]
tuple[str, ...]
set[uuid.UUID]
```

**Optional:**
Implicit Optional via union with None:
```python
path: Path | None = None
```

---

*Convention analysis: 2026-07-07*
