"""Worker CLI entrypoint: parser construction, command dispatch, top-level errors.

STRUCT-03: domain command logic lives in `worker/commands/*`; parser
construction lives in `worker/parser.py`. This module is routing-only.
D-30 (20-12): pure dispatch lookup, no per-command special cases -- every
`args.command` resolves through `DISPATCH.get(args.command)`.
"""

from __future__ import annotations

from trading_platform.worker.commands import DISPATCH
from trading_platform.worker.parser import build_parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handler = DISPATCH.get(args.command)
    if handler is None:
        parser.error(f"Unknown command: {args.command}")
        return
    handler(args)


if __name__ == "__main__":
    main()
