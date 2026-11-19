"""Run the first signals study from its protocol (docs/SIGNALS-PLAN.md, Step 1).

Usage (research database, migrated to head):

    TRADING_PLATFORM_RESEARCH__MODE=true TRADING_PLATFORM_DATABASE__NAME=trading_research \\
        TRADING_PLATFORM_RESEARCH__CALENDAR_START=2014-01-02 \\
        PYTHONPATH=src python scripts/run_signals_v2_study.py \\
        --protocol config/research/signals_v2_study.yaml --out docs/studies/signals-v2-base-risk-2026-11-19

Writes ``results.json`` and ``REPORT.md`` into ``--out``. It exits with 3 for missing
data and 4 for failed engine conformance. Only a pending-data result may be
overwritten by a later run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from yaml import YAMLError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trading_platform.core.settings import load_settings  # noqa: E402
from trading_platform.services.research.signal_study import (  # noqa: E402
    STATUS_COMPLETED,
    STATUS_FAILED_CONFORMANCE,
    STATUS_PENDING_DATA,
    HoldoutAlreadyExposedError,
    load_protocol,
    render_report,
    run_signal_study,
)

TRADING_DATABASE = "trading_platform"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/run_signals_v2_study.py")
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    settings = load_settings()
    if not settings.research.mode or settings.database.name == TRADING_DATABASE:
        print("Refusing: run in research mode against the research database.", file=sys.stderr)
        return 2
    results_path = args.out / "results.json"
    if results_path.exists():
        previous_status = json.loads(results_path.read_text()).get("status")
        if previous_status != STATUS_PENDING_DATA:
            print(f"Refusing: {results_path} already holds a study result ({previous_status}).", file=sys.stderr)
            return 2

    try:
        protocol, digest = load_protocol(args.protocol)
    except (OSError, ValueError, YAMLError) as exc:
        print(f"Invalid study protocol: {exc}", file=sys.stderr)
        return 2
    try:
        result = run_signal_study(settings, protocol, protocol_sha256=digest, progress=lambda m: print(m, flush=True))
    except HoldoutAlreadyExposedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (args.out / "REPORT.md").write_text(render_report(result))
    print(f"status: {result['status']}; wrote {results_path}")
    return {STATUS_COMPLETED: 0, STATUS_PENDING_DATA: 3, STATUS_FAILED_CONFORMANCE: 4}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
