"""Unit tests for the log-sanitization chokepoint (`core/log_sanitizer.py`)."""

from __future__ import annotations

import logging

import pytest

from trading_platform.core import logging as core_logging
from trading_platform.core.log_sanitizer import REDACTION, mask_order_id, sanitize

# ---------------------------------------------------------------------------
# Task 1: credential/token/conn-URL/auth-header redaction
# ---------------------------------------------------------------------------


def test_sanitize_redacts_password_key():
    result = sanitize({"password": "hunter2"})
    assert result["password"] == REDACTION
    assert "hunter2" not in str(result)


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "passwd",
        "api_key",
        "API_KEY",
        "api-key",
        "api_secret",
        "secret",
        "token",
        "authorization",
        "Authorization",
    ],
)
def test_sanitize_redacts_credential_keys_case_insensitive(key):
    result = sanitize({key: "super-secret-value"})
    assert result[key] == REDACTION


def test_sanitize_redacts_nested_credential_keys():
    payload = {
        "outer": {
            "inner": {
                "password": "hunter2",
                "safe_field": "keep-me",
            }
        }
    }
    result = sanitize(payload)
    assert result["outer"]["inner"]["password"] == REDACTION
    assert result["outer"]["inner"]["safe_field"] == "keep-me"


def test_sanitize_redacts_password_bearing_connection_url():
    payload = {"db_url": "postgresql+psycopg://user:hunter2@host/db"}
    result = sanitize(payload)
    assert "hunter2" not in result["db_url"]
    assert result["db_url"] == f"postgresql+psycopg://user:{REDACTION}@host/db"


def test_sanitize_redacts_authorization_header_value():
    payload = {"headers": {"Authorization": "Bearer abc123"}}
    result = sanitize(payload)
    assert result["headers"]["Authorization"] != "Bearer abc123"
    assert "abc123" not in str(result)


def test_sanitize_scrubs_embedded_secret_in_free_text_string():
    payload = {"message": "connecting with password=hunter2 to the db"}
    result = sanitize(payload)
    assert "hunter2" not in result["message"]
    assert REDACTION in result["message"]


def test_sanitize_scrubs_embedded_api_key_in_free_text_string():
    payload = {"message": "request failed, api_key=sk-abc123xyz was rejected"}
    result = sanitize(payload)
    assert "sk-abc123xyz" not in result["message"]


def test_sanitize_is_pure_does_not_mutate_input():
    payload = {"password": "hunter2", "nested": {"api_key": "abc123"}}
    original_password = payload["password"]
    original_nested_key = payload["nested"]["api_key"]

    result = sanitize(payload)

    assert payload["password"] == original_password
    assert payload["nested"]["api_key"] == original_nested_key
    assert result is not payload
    assert result["nested"] is not payload["nested"]


def test_sanitize_leaves_non_sensitive_fields_untouched():
    payload = {"strategy_id": "trend_following_daily", "run_id": "abc-123"}
    result = sanitize(payload)
    assert result == payload


def test_sanitize_handles_lists_of_dicts():
    payload = {"items": [{"password": "hunter2"}, {"safe": "ok"}]}
    result = sanitize(payload)
    assert result["items"][0]["password"] == REDACTION
    assert result["items"][1]["safe"] == "ok"


# ---------------------------------------------------------------------------
# Task 2: broker-order-id masking + emit_structured_log wiring + get_logger
# ---------------------------------------------------------------------------


def test_mask_order_id_masks_to_last_six_by_default():
    result = mask_order_id("abcdef123456")
    assert result != "abcdef123456"
    assert result.endswith("123456")
    assert "abcdef" not in result


def test_mask_order_id_unmask_returns_full_value():
    result = mask_order_id("abcdef123456", unmask=True)
    assert result == "abcdef123456"


def test_mask_order_id_short_value_returned_unchanged():
    assert mask_order_id("abc123") == "abc123"
    assert mask_order_id("ab") == "ab"


def test_mask_order_id_non_string_value_returned_unchanged():
    assert mask_order_id(None) is None
    assert mask_order_id(12345) == 12345


@pytest.mark.parametrize("key", ["broker_order_id", "order_id", "client_order_id"])
def test_sanitize_masks_order_id_keys_by_default(key):
    result = sanitize({key: "abcdef123456"})
    assert result[key] != "abcdef123456"
    assert result[key].endswith("123456")


def test_sanitize_unmask_ids_reveals_full_order_id():
    result = sanitize({"broker_order_id": "abcdef123456"}, unmask_ids=True)
    assert result["broker_order_id"] == "abcdef123456"


def test_sanitize_masks_order_id_and_redacts_password_together():
    payload = {"password": "hunter2", "broker_order_id": "abcdef123456"}
    result = sanitize(payload)
    assert result["password"] == REDACTION
    assert result["broker_order_id"] != "abcdef123456"
    assert result["broker_order_id"].endswith("123456")


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_emit_structured_log_sanitizes_context_end_to_end():
    logger = logging.getLogger("test.log_sanitizer.e2e")
    logger.setLevel(logging.INFO)
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.propagate = False

    try:
        core_logging.emit_structured_log(
            logger,
            logging.INFO,
            "order submitted",
            strategy_id="trend_following_daily",
            password="hunter2",
            broker_order_id="abcdef123456",
        )
    finally:
        logger.removeHandler(handler)

    assert len(handler.records) == 1
    context = handler.records[0].context
    assert context["password"] == REDACTION
    assert context["broker_order_id"] != "abcdef123456"
    assert context["broker_order_id"].endswith("123456")
    assert "hunter2" not in str(context)


def test_emit_structured_log_unmasks_when_debug_flag_set():
    logger = logging.getLogger("test.log_sanitizer.e2e.unmask")
    logger.setLevel(logging.INFO)
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.propagate = False

    original = core_logging._DEBUG_UNMASK_IDS
    core_logging._DEBUG_UNMASK_IDS = True
    try:
        core_logging.emit_structured_log(
            logger,
            logging.INFO,
            "order submitted",
            broker_order_id="abcdef123456",
        )
    finally:
        core_logging._DEBUG_UNMASK_IDS = original
        logger.removeHandler(handler)

    context = handler.records[0].context
    assert context["broker_order_id"] == "abcdef123456"


def test_get_logger_returns_standard_logger():
    logger = core_logging.get_logger("trading_platform.test_module")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "trading_platform.test_module"
