"""Source identifiers for research runs (proposal Part F/J.2).

Legacy runs record Git HEAD with a dirty marker. Signals studies and risk runs
record Git HEAD or a content digest for local source.
"""

from __future__ import annotations

import subprocess
from base64 import urlsafe_b64encode
from functools import lru_cache
from hashlib import sha256

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


def study_code_identity() -> str:
    """Identify new study code by clean Git HEAD or a SHA-256 of the local source.

    Dirty checkouts include untracked execution code. The source digest covers the
    application, migration and runner Python files and project dependency manifests.
    The URL-safe encoding fits in existing 48-character provenance fields.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        if head and not status:
            return head
    except (OSError, subprocess.SubprocessError):
        pass

    source_files = [
        path
        for directory in ("src", "alembic", "scripts")
        for path in (PROJECT_ROOT / directory).rglob("*.py")
    ]
    if not source_files:
        return UNKNOWN_CODE_SHA
    source_files.extend(
        PROJECT_ROOT / name
        for name in ("pyproject.toml", "uv.lock", "poetry.lock", "alembic.ini")
        if (PROJECT_ROOT / name).is_file()
    )
    digest = sha256()
    for path in sorted(source_files):
        relative_path = path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8")
        digest.update(relative_path + b"\0" + sha256(path.read_bytes()).digest())
    return "src:" + urlsafe_b64encode(digest.digest()).decode("ascii").rstrip("=")
