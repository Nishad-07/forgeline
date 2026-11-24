from __future__ import annotations

import pytest

from app.errors import InvalidStateError, NotFoundError
from app.models import PaymentStatus
from app.notifications import get_outbox
from app.payments import create_payment
from app.refunds import list_refunds, refund_payment
from tests.conftest import VALID_CARD


def test_full_refund(captured_payment):
    refund = refund_payment(captured_payment.id, reason="customer request")
    assert refund.amount_paise == captured_payment.amount_paise
    assert captured_payment.status == PaymentStatus.REFUNDED
    assert list_refunds(captured_payment.id) == [refund]
    assert get_outbox(captured_payment.merchant_id)[-1]["event"] == "refund.created"


def test_cannot_refund_twice(captured_payment):
    refund_payment(captured_payment.id)
    with pytest.raises(InvalidStateError) as exc:
        refund_payment(captured_payment.id)
    assert exc.value.code == "PAYMENT_NOT_REFUNDABLE"


def test_cannot_refund_uncaptured(merchant):
    payment = create_payment(merchant.id, 5000, VALID_CARD)
    with pytest.raises(InvalidStateError) as exc:
        refund_payment(payment.id)
    assert exc.value.code == "PAYMENT_NOT_REFUNDABLE"


def test_list_refunds_unknown_payment():
    with pytest.raises(NotFoundError):
        list_refunds("pay_missing")
