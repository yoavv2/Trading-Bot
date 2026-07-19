"""Worker CLI handler: `run-jobs` (STRUCT-03: extracted from __main__.py).

This module is a thin wrapper only -- it contains no queue, claim, or
status-transition logic of its own. All framework behavior lives in
``trading_platform.jobs.runner``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket

from trading_platform.core.logging import configure_logging, get_logger
from trading_platform.core.startup import enforce_startup_config
from trading_platform.jobs.registry import build_default_registry
from trading_platform.jobs.runner import run_worker_loop
from trading_platform.services.config.validation import ExecutionMode


def run_jobs_command(args: argparse.Namespace) -> None:
    settings = enforce_startup_config(mode=ExecutionMode.PAPER)
    configure_logging(settings.logging)
    logger = get_logger("trading_platform.worker")

    # Computed here, not in the framework, so jobs/runner.py stays free of
    # process concerns (T-17-09-06: worker IDs are advisory audit labels,
    # not an authorization/uniqueness mechanism).
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"

    registry = build_default_registry(settings)
    report = run_worker_loop(
        worker_id=worker_id,
        registry=registry,
        max_jobs=args.max_jobs,
        once=args.once,
        settings=settings,
    )

    logger.info("worker_run_jobs_completed", extra={"context": report})
    indent = None if args.compact else 2
    print(json.dumps(report, indent=indent, default=str))
