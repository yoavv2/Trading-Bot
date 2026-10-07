"""Create and migrate the isolated research database (deployment tooling).

Usage (planning doc ``01-IMPLEMENTATION-PLAN.md`` S0):

    TRADING_PLATFORM_DATABASE__NAME=trading_research python scripts/create_research_db.py
    python scripts/create_research_db.py --database trading_research
    python scripts/create_research_db.py --database trading_research --restore-inputs .data/research/freezes/<id>/inputs

The script refuses the trading database name (``trading_platform`` by default) so it
can never upgrade the main database. It creates the target database when missing,
upgrades it to the single Alembic head (0001-0030, then 0031_research_platform) and
prints the server and database it targeted. ``--restore-inputs`` loads a preserved
``inputs/`` export (bars, sessions, symbols) and verifies the manifest digest.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.migrate import build_alembic_config  # noqa: E402

from trading_platform.core.settings import clear_settings_cache, load_settings  # noqa: E402

PROTECTED_DATABASE_NAMES = {"trading_platform"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/create_research_db.py")
    parser.add_argument(
        "--database",
        default=None,
        help="Research database name (defaults to TRADING_PLATFORM_DATABASE__NAME).",
    )
    parser.add_argument(
        "--restore-inputs",
        default=None,
        help="Path to a preserved inputs/ export to load after migration.",
    )
    return parser


def describe_target() -> dict[str, str]:
    settings = load_settings()
    return {
        "host": settings.database.host,
        "port": str(settings.database.port),
        "database": settings.database.name,
        "user": settings.database.user,
    }


def create_database_if_missing(database: str) -> bool:
    settings = load_settings()
    admin = psycopg.connect(
        host=settings.database.host,
        port=settings.database.port,
        user=settings.database.user,
        password=settings.database.password,
        dbname=os.getenv("TRADING_PLATFORM_ADMIN_DB", "postgres"),
        autocommit=True,
    )
    try:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
        if exists:
            return False
        admin.execute(f'CREATE DATABASE "{database}"')
        return True
    finally:
        admin.close()


def upgrade_to_head() -> None:
    command.upgrade(build_alembic_config(), "head")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database:
        os.environ["TRADING_PLATFORM_DATABASE__NAME"] = args.database
    clear_settings_cache()
    target = describe_target()
    if target["database"] in PROTECTED_DATABASE_NAMES:
        print(
            f"Refusing: '{target['database']}' is the trading database. "
            "Pass --database trading_research (or another research name).",
            file=sys.stderr,
        )
        return 2

    created = create_database_if_missing(target["database"])
    clear_settings_cache()
    upgrade_to_head()
    print(
        f"research database ready: host={target['host']} port={target['port']} "
        f"database={target['database']} user={target['user']} created={created}"
    )

    if args.restore_inputs:
        from trading_platform.db.session import session_scope
        from trading_platform.services.research.freeze import restore_inputs

        with session_scope(load_settings()) as session:
            report = restore_inputs(session, Path(args.restore_inputs))
        print(
            f"inputs restored: bars={report.bars_inserted} sessions={report.sessions_inserted} "
            f"symbols={report.symbols_inserted} digest_verified={report.digest_verified}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
