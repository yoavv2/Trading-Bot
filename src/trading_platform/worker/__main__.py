"""Worker CLI entrypoint: parser construction, command dispatch, top-level errors.

STRUCT-03: domain command logic lives in `worker/commands/*`; parser
construction lives in `worker/parser.py`. This module is routing-only.
"""

from __future__ import annotations

from trading_platform.worker.commands import DISPATCH, run_dry_bootstrap, run_placeholder_worker
from trading_platform.worker.parser import build_parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        run_placeholder_worker(args.interval_seconds)
        return
    if args.command == "dry-run":
        run_dry_bootstrap(args.strategy)
        return

    handler = DISPATCH.get(args.command)
    if handler is None:
        parser.error(f"Unknown command: {args.command}")
        return
    handler(args)


if __name__ == "__main__":
    main()
