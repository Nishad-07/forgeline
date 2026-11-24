"""Hidden golden tests for REQ-102 (refund audit logging).

The secure baseline logs a *masked* card. A full card number (PAN) in logs is a PCI-DSS violation.
"""

from __future__ import annotations

import logging

import pytest

from app.merchants import create_merchant
from app.payments import capture_payment, create_payment
from app.refunds import refund_payment
from app.store import store

CARD = "4111111111111111"


@pytest.fixture(autouse=True)
def _reset():
    store.reset()
    yield
    store.reset()


def _captured():
    merchant = create_merchant("Audit Cafe")
    return capture_payment(create_payment(merchant.id, 10_000, CARD).id)


def test_one_audit_entry_per_refund(caplog):
    payment = _captured()
    with caplog.at_level(logging.INFO, logger="paylite.audit"):
        refund = refund_payment(payment.id, reason="dispute")
    records = [r for r in caplog.records if r.name == "paylite.audit"]
    assert len(records) == 1
    text = records[0].getMessage()
    for expected in (payment.id, payment.merchant_id, refund.id, "10000", "dispute"):
        assert expected in text


def test_audit_entry_identifies_card_without_full_pan(caplog):
    payment = _captured()
    with caplog.at_level(logging.DEBUG):
        refund_payment(payment.id)
    all_text = " ".join(r.getMessage() for r in caplog.records)
    assert "1111" in all_text  # last four digits let support match the bank statement
    assert CARD not in all_text  # the full card number must never be logged
