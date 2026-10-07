"""Executed code identity recorded on every research run (proposal Part F/J.2).

``code_sha`` is the git HEAD commit plus a ``-dirty`` suffix when the working tree
differs from it, so a result can always be traced to the code that produced it; when
git is unavailable the value is ``"unknown"`` and the run records that honestly.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache

from trading_platform.core.settings import PROJECT_ROOT

UNKNOWN_CODE_SHA = "unknown"


@lru_cache(maxsize=1)
def code_sha() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_CODE_SHA
    if not head:
        return UNKNOWN_CODE_SHA
    return f"{head}-dirty" if status else head
