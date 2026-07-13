"""Unit tests for the reconciliation type contracts (RECON-05, RECON-07).

No DB, ORM, or broker fixtures — this module is pure value types, so these tests
construct dataclasses directly with plain Python values.
"""

from __future__ import annotations

import pytest

from trading_platform.services.reconciliation_types import Finding, ReconciliationFinding


# --- Task 1: closed ReconciliationFinding enum + Finding value object -----------------


def test_reconciliation_finding_has_exactly_five_members():
    assert len(ReconciliationFinding) == 5
    assert {member.name for member in ReconciliationFinding} == {
        "MISSING_LOCAL",
        "MISSING_BROKER",
        "QUANTITY_MISMATCH",
        "PRICE_MISMATCH",
        "STATE_MISMATCH",
    }


def test_reconciliation_finding_rejects_unknown_string():
    with pytest.raises(ValueError):
        ReconciliationFinding("not_a_finding")


def test_finding_category_is_typed_as_enum():
    finding = Finding(
        category=ReconciliationFinding.QUANTITY_MISMATCH,
        identity=None,
        severity="error",
        blocks_execution=True,
        message="quantity mismatch",
        details={"expected": 1, "actual": 2},
    )
    assert isinstance(finding.category, ReconciliationFinding)
    assert finding.category is ReconciliationFinding.QUANTITY_MISMATCH


def test_finding_to_event_dict_matches_execution_event_shape():
    finding = Finding(
        category=ReconciliationFinding.STATE_MISMATCH,
        identity=None,
        severity="warning",
        blocks_execution=False,
        message="state drift",
        details={"local": "filled", "broker": "partially_filled"},
        paper_order_id="abc-123",
    )
    event = finding.to_event_dict()
    assert event == {
        "event_type": "STATE_MISMATCH",
        "severity": "warning",
        "blocks_execution": False,
        "message": "state drift",
        "paper_order_id": "abc-123",
        "details": {"local": "filled", "broker": "partially_filled"},
    }


def test_finding_to_event_dict_defaults_paper_order_id_to_none():
    finding = Finding(
        category=ReconciliationFinding.MISSING_BROKER,
        identity=None,
        severity="error",
        blocks_execution=True,
        message="missing on broker",
        details={},
    )
    assert finding.to_event_dict()["paper_order_id"] is None
