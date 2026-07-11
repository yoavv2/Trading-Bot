from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Mirror the per-test sys.path shims so `trading_platform` and `tests` import
# cleanly regardless of how pytest is invoked.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from trading_platform.core import settings as _settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the operator's real .env out of the test process (00-VERIFY step 1).

    ``EnvironmentOverrides`` hardcodes ``env_file=".env"``. Loading it bleeds the
    operator's live environment into every test — flipping ``app.environment``
    from ``test`` to ``local`` and, when the operator file is malformed, injecting
    ``None`` values that fail settings validation before a test body even runs.

    Disabling dotenv loading for the whole suite makes tests depend only on their
    own fixtures / ``monkeypatch`` calls. DB-backed fixtures read connection
    details from ``os.getenv(..., "localhost")`` and set them explicitly, so they
    are unaffected by this.
    """
    monkeypatch.setitem(_settings.EnvironmentOverrides.model_config, "env_file", None)
    _settings.clear_settings_cache()
    yield
    _settings.clear_settings_cache()
