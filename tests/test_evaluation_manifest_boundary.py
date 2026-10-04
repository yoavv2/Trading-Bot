"""Completeness guard for the evaluation input manifest (PROV-01, 04 task 1a).

Strategy code, risk code and portfolio code must read market data ONLY through
the recorded shared accessors in ``services/market_data_access.py``. A direct
query against the bar or session tables would be invisible to the manifest, so
this AST scan fails if any such code references the ``DailyBar`` /
``MarketSession`` models (or an alias) or imports their model modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trading_platform"

_FORBIDDEN_NAMES = frozenset({"DailyBar", "MarketSession", "DailyBarModel"})
_FORBIDDEN_MODULE_SUFFIXES = ("db.models.daily_bar", "db.models.market_session")


def find_direct_market_table_reads(source_text: str) -> list[str]:
    """Return one description per direct reference to the bar/session models."""

    violations: list[str] = []
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith(_FORBIDDEN_MODULE_SUFFIXES):
                violations.append(f"line {node.lineno}: import from {module}")
            if module.startswith("trading_platform.db.models"):
                for alias in node.names:
                    if alias.name in _FORBIDDEN_NAMES:
                        violations.append(f"line {node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(_FORBIDDEN_MODULE_SUFFIXES):
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            violations.append(f"line {node.lineno}: references {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            violations.append(f"line {node.lineno}: references .{node.attr}")
    return violations


def _guarded_files() -> list[Path]:
    files = sorted((SRC / "strategies").rglob("*.py"))
    files += [SRC / "services" / "risk.py", SRC / "services" / "portfolio.py"]
    return files


def test_boundary_scanner_rejects_a_direct_dailybar_query_in_strategy_code() -> None:
    synthetic = (
        "from sqlalchemy import select\n"
        "from trading_platform.db.models import DailyBar\n"
        "\n"
        "def generate_signals(db_session, as_of):\n"
        "    return db_session.execute(select(DailyBar)).scalars().all()\n"
    )

    violations = find_direct_market_table_reads(synthetic)

    assert violations
    assert any("imports DailyBar" in item for item in violations)
    assert any("references DailyBar" in item for item in violations)


def test_boundary_scanner_accepts_accessor_only_source() -> None:
    accessor_only = (
        "from trading_platform.services.market_data_access import bars_for_sessions\n"
        "\n"
        "def generate_signals(db_session, as_of):\n"
        "    return bars_for_sessions(db_session, symbol='AAPL', n_sessions=3, as_of=as_of)\n"
    )

    assert find_direct_market_table_reads(accessor_only) == []


def test_boundary_scanner_catches_aliased_and_module_path_imports() -> None:
    assert find_direct_market_table_reads("from trading_platform.db.models import DailyBar as D\n")
    assert find_direct_market_table_reads("from trading_platform.db.models import MarketSession as M\n")
    assert find_direct_market_table_reads(
        "from trading_platform.db.models.daily_bar import DailyBar as DailyBarModel\n"
    )
    assert find_direct_market_table_reads("import trading_platform.db.models.market_session as ms\n")
    assert find_direct_market_table_reads(
        "import trading_platform.db.models as models\nx = models.DailyBar\n"
    )


def test_real_strategy_and_risk_code_read_market_data_only_through_recorded_accessors() -> None:
    files = _guarded_files()
    assert len(files) >= 6  # strategies/** plus risk.py and portfolio.py

    offenders = {
        str(path.relative_to(SRC)): violations
        for path in files
        if (violations := find_direct_market_table_reads(path.read_text()))
    }

    assert offenders == {}
