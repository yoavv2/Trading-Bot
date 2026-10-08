"""Prepare the persistent research database for the research product (idempotent).

Usage (``make research-bootstrap`` passes the research environment):

    TRADING_PLATFORM_RESEARCH__MODE=true TRADING_PLATFORM_DATABASE__NAME=trading_research \\
        TRADING_PLATFORM_RESEARCH__CALENDAR_START=2014-01-02 \\
        PYTHONPATH=src python scripts/bootstrap_research.py [--refresh-catalog] [--sessions-through YYYY-MM-DD]

Creates the database when missing and upgrades it to the single Alembic head
(``scripts/create_research_db.py``; refuses the trading database name), then runs
``services.research.bootstrap.bootstrap_research_database``: example versions, public
asset catalog, exchange sessions from the pinned calendar start. Safe to re-run; never
clears existing drafts, versions, lists, studies or results.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_research_db import (  # noqa: E402
    PROTECTED_DATABASE_NAMES,
    create_database_if_missing,
    describe_target,
    upgrade_to_head,
)

from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402
from trading_platform.services.research.bootstrap import (  # noqa: E402
    ResearchBootstrapError,
    bootstrap_research_database,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/bootstrap_research.py")
    parser.add_argument(
        "--database", default=None, help="Research database name (default: settings)."
    )
    parser.add_argument(
        "--refresh-catalog",
        action="store_true",
        help="Re-download the public catalog even when populated.",
    )
    parser.add_argument(
        "--sessions-through",
        default=None,
        help="Last session date to store (default: the calendar's last available session).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database:
        os.environ["TRADING_PLATFORM_DATABASE__NAME"] = args.database
    clear_settings_cache()
    target = describe_target()
    if target["database"] in PROTECTED_DATABASE_NAMES:
        print(f"Refusing: '{target['database']}' is the trading database.", file=sys.stderr)
        return 2
    created = create_database_if_missing(target["database"])
    clear_settings_cache()
    upgrade_to_head()
    clear_settings_cache()
    through = date.fromisoformat(args.sessions_through) if args.sessions_through else None
    try:
        report = bootstrap_research_database(
            load_settings(),
            refresh_catalog=args.refresh_catalog,
            sessions_through=through,
            created=created,
        )
    except ResearchBootstrapError as exc:
        print(f"Refusing ({exc.code}): {exc}", file=sys.stderr)
        return 2
    summary = report.to_dict()
    print(
        f"research database ready: host={target['host']} port={target['port']} "
        f"database={target['database']} created={created} alembic=head"
    )
    print(f"example versions: {len(summary['example_versions'])} present")
    print(
        f"catalog: {summary['catalog']['action']} named={summary['catalog']['named']} "
        f"total={summary['catalog']['total']}"
    )
    sessions = summary["market_sessions"]
    print(
        f"market sessions ({sessions['exchange']}): {sessions['action']} "
        f"{sessions['from']}..{sessions['through']} count={sessions['count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
