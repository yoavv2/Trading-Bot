"""Structured logging helpers for the trading platform."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from trading_platform.core.settings import LoggingSettings


class JsonLogFormatter(logging.Formatter):
    """Keep startup and worker logs machine-readable from day one."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: LoggingSettings) -> None:
    level = getattr(logging, settings.level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    logging.captureWarnings(True)


def build_log_context(
    *,
    strategy_id: str | None = None,
    run_id: str | None = None,
    session_date: str | None = None,
    strategy_status: str | None = None,
    blocked_reason: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    standard_fields = {
        "strategy_id": strategy_id,
        "run_id": run_id,
        "session_date": session_date,
        "strategy_status": strategy_status,
        "blocked_reason": blocked_reason,
    }
    for field_name, value in standard_fields.items():
        if value is not None:
            context[field_name] = value

    for field_name, value in extra.items():
        if value is not None:
            context[field_name] = value

    return context


def emit_structured_log(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    strategy_id: str | None = None,
    run_id: str | None = None,
    session_date: str | None = None,
    strategy_status: str | None = None,
    blocked_reason: str | None = None,
    **extra: Any,
) -> None:
    logger.log(
        level,
        message,
        extra={
            "context": build_log_context(
                strategy_id=strategy_id,
                run_id=run_id,
                session_date=session_date,
                strategy_status=strategy_status,
                blocked_reason=blocked_reason,
                **extra,
            )
        },
    )
