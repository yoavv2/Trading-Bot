"""Single sanitization chokepoint for structured log payloads.

`sanitize()` is the one function every log payload must pass through before
it reaches a logger (see `core/logging.py::emit_structured_log`). It redacts
credentials, API keys, tokens, password-bearing connection URLs, and
Authorization headers, and masks broker order IDs to a last-6 form unless an
explicit debug-unmask flag is set.

Dependency-free: stdlib `re` only.
"""

from __future__ import annotations

import re
from typing import Any

REDACTION = "[REDACTED]"

# Keys (case-insensitive) whose values are always fully redacted, regardless
# of nesting depth, when found in a dict.
SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|api[_-]?key|api[_-]?secret|secret|token|authorization|auth)",
    re.IGNORECASE,
)

# Keys (case-insensitive) whose values identify a broker order and must be
# masked to a last-6 form unless the debug-unmask flag is set.
ORDER_ID_KEY_PATTERN = re.compile(
    r"^(broker_order_id|order_id|client_order_id)$",
    re.IGNORECASE,
)

# Embedded `key=value` secret pairs inside a free-text string, e.g.
# "connecting with password=hunter2" or "api_key=abc123".
_EMBEDDED_SECRET_PATTERN = re.compile(
    r"(?P<key>password|passwd|api[_-]?key|api[_-]?secret|secret|token)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>[^\s&,;]+)",
    re.IGNORECASE,
)

# Connection URLs with an embedded password: scheme://user:PASSWORD@host
_CONN_URL_PASSWORD_PATTERN = re.compile(r"(://[^:@/\s]+:)([^@\s]+)(@)")

# Bearer/token-style Authorization header values, e.g. "Bearer abc123".
_BEARER_TOKEN_PATTERN = re.compile(r"^(Bearer|Token|Basic)\s+\S+$", re.IGNORECASE)


def _scrub_string(value: str) -> str:
    """Scrub a plain string value of embedded secrets, conn-URL passwords,
    and bearer-style auth tokens. Always returns a (possibly unchanged) new
    string; never mutates in place since strings are immutable.
    """
    scrubbed = value

    if _BEARER_TOKEN_PATTERN.match(scrubbed):
        scheme = scrubbed.split(" ", 1)[0]
        return f"{scheme} {REDACTION}"

    scrubbed = _CONN_URL_PASSWORD_PATTERN.sub(
        lambda m: f"{m.group(1)}{REDACTION}{m.group(3)}", scrubbed
    )

    scrubbed = _EMBEDDED_SECRET_PATTERN.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{REDACTION}", scrubbed
    )

    return scrubbed


def mask_order_id(value: Any, *, unmask: bool = False) -> Any:
    """Mask a broker order id to its last-6 characters by default.

    Returns the value unchanged when `unmask` is True, when the value isn't
    a string, or when the string is 6 characters or fewer (nothing useful
    left to hide).
    """
    if unmask:
        return value
    if not isinstance(value, str):
        return value
    if len(value) <= 6:
        return value
    return f"...{value[-6:]}"


def sanitize(payload: Any, *, unmask_ids: bool = False) -> Any:
    """Recursively redact sensitive values from `payload`.

    Returns a NEW structure; `payload` (and any nested dict/list within it)
    is never mutated. Dicts have keys checked against `SENSITIVE_KEY_PATTERN`
    (full redaction) and `ORDER_ID_KEY_PATTERN` (last-6 masking, unless
    `unmask_ids` is True); string values are scrubbed of embedded secrets,
    password-bearing connection URLs, and bearer/token-style auth headers.
    """
    if isinstance(payload, dict):
        sanitized: dict[Any, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and SENSITIVE_KEY_PATTERN.search(key):
                sanitized[key] = REDACTION
            elif isinstance(key, str) and ORDER_ID_KEY_PATTERN.match(key):
                sanitized[key] = mask_order_id(value, unmask=unmask_ids)
            else:
                sanitized[key] = sanitize(value, unmask_ids=unmask_ids)
        return sanitized

    if isinstance(payload, list):
        return [sanitize(item, unmask_ids=unmask_ids) for item in payload]

    if isinstance(payload, tuple):
        return tuple(sanitize(item, unmask_ids=unmask_ids) for item in payload)

    if isinstance(payload, str):
        return _scrub_string(payload)

    return payload
