from datetime import date
from decimal import Decimal

import pytest

from trading_platform.db.models import BacktestSignal, BacktestTrade
from trading_platform.services.read_recording import sha256_hex
from trading_platform.services.research import code_identity
from trading_platform.services.research.backtest import results_digest_for

SIGNAL_DATE = date(2025, 1, 2)
FILL_DATE = date(2025, 1, 3)
EXPIRY_COUNTS = {"expired_below_stop": 1}


def _trade() -> BacktestTrade:
    return BacktestTrade(
        entry_signal_session=SIGNAL_DATE,
        entry_fill_session=FILL_DATE,
        exit_fill_session=FILL_DATE,
        quantity=Decimal("10"),
        entry_price=Decimal("100"),
        exit_price=Decimal("95"),
        net_pnl=Decimal("-50"),
        rounding_slack=Decimal("0"),
        status="closed",
        reference_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        rule_entry_price=Decimal("100"),
        rule_exit_price=Decimal("95"),
        exit_reason="stop",
        intrabar_order_unknown=False,
    )


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("reference_price", Decimal("99")),
        ("stop_price", Decimal("94")),
        ("target_price", Decimal("111")),
        ("rule_entry_price", Decimal("99")),
        ("rule_exit_price", Decimal("94")),
        ("exit_reason", "rule"),
        ("intrabar_order_unknown", True),
    ],
)
def test_risk_metadata_changes_v2_digest_only(field: str, new_value: object) -> None:
    trade = _trade()
    legacy = sha256_hex(
        {
            "trades": [
                [
                    FILL_DATE.isoformat(),
                    FILL_DATE.isoformat(),
                    "10",
                    "100",
                    "95",
                    "-50",
                    "0",
                    "closed",
                ]
            ],
            "equity": [],
        }
    )
    risk = results_digest_for([trade], [], expired_by_reason=EXPIRY_COUNTS)
    assert risk != legacy
    assert results_digest_for([trade], []) == legacy
    setattr(trade, field, new_value)
    assert results_digest_for([trade], []) == legacy
    assert results_digest_for([trade], [], expired_by_reason=EXPIRY_COUNTS) != risk


def test_v2_digest_captures_expiry_at_signal_level() -> None:
    signal = BacktestSignal(
        session_date=SIGNAL_DATE,
        signal_metadata={
            "entry_outcome": "expired_below_stop",
            "fill_session": FILL_DATE.isoformat(),
            "levels": {"reference": "100", "stop": "95", "target": "110"},
        },
    )
    expected = results_digest_for(
        [], [], expired_by_reason=EXPIRY_COUNTS, expired_signals=(signal,)
    )
    assert results_digest_for([], [], expired_by_reason=EXPIRY_COUNTS) != expected
    signal.session_date = date(2025, 1, 1)
    assert (
        results_digest_for([], [], expired_by_reason=EXPIRY_COUNTS, expired_signals=(signal,))
        != expected
    )


def test_v2_digest_is_stable_for_trades_with_the_same_fill_dates() -> None:
    first, second = _trade(), _trade()
    second.exit_reason = "target"
    assert results_digest_for([first, second], [], expired_by_reason={}) == results_digest_for(
        [second, first], [], expired_by_reason={}
    )


def test_study_code_identity_uses_clean_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    head = "a" * 40

    def run_git(command, **kwargs):
        output = f"{head}\n" if command[1] == "rev-parse" else ""
        return code_identity.subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(code_identity.subprocess, "run", run_git)
    assert code_identity.study_code_identity() == head


def test_study_code_identity_changes_with_local_source(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code_identity, "PROJECT_ROOT", tmp_path)

    def git_unavailable(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr(code_identity.subprocess, "run", git_unavailable)
    source = tmp_path / "src"
    source.mkdir()
    module = source / "study.py"
    module.write_text("VALUE = 1\n")
    first = code_identity.study_code_identity()
    assert first.startswith("src:")
    assert len(first) == 47
    assert code_identity.study_code_identity() == first
    module.write_text("VALUE = 2\n")
    assert code_identity.study_code_identity() != first
    module.write_text("VALUE = 1\n")
    assert code_identity.study_code_identity() == first
