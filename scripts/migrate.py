"""Apply Alembic migrations using the project settings loader."""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from trading_platform.core.settings import load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    settings = load_settings()
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database.url)
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/migrate.py")
    parser.add_argument("command", nargs="?", choices=["upgrade", "downgrade", "current"], default="upgrade")
    parser.add_argument("revision", nargs="?", default="head")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = build_alembic_config()

    if args.command == "upgrade":
        command.upgrade(config, args.revision)
        return
    if args.command == "downgrade":
        command.downgrade(config, args.revision)
        return
    command.current(config)


if __name__ == "__main__":
    main()
