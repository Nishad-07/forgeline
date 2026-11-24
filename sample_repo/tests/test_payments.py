from __future__ import annotations

import pytest

from app.errors import InvalidStateError, ValidationError
from app.merchants import deactivate_merchant
from app.models import PaymentStatus
from app.notifications import get_outbox
from app.payments import capture_payment, create_payment, luhn_valid
from tests.conftest import VALID_CARD


def test_luhn():
    assert luhn_valid(VALID_CARD)
    assert not luhn_valid("4111111111111112")


def test_create_payment(merchant):
    payment = create_payment(merchant.id, 25_000, VALID_CARD)
    assert payment.status == PaymentStatus.CREATED
    assert payment.masked_card == "**** **** **** 1111"
    assert get_outbox(merchant.id)[-1]["event"] == "payment.created"


@pytest.mark.parametrize("amount", [0, -100, 10_000_001])
def test_invalid_amount(merchant, amount):
    with pytest.raises(ValidationError) as exc:
        create_payment(merchant.id, amount, VALID_CARD)
    assert exc.value.code == "INVALID_AMOUNT"


def test_invalid_card(merchant):
    with pytest.raises(ValidationError) as exc:
        create_payment(merchant.id, 1000, "1234")
    assert exc.value.code == "INVALID_CARD"


def test_unsupported_currency(merchant):
    with pytest.raises(ValidationError) as exc:
        create_payment(merchant.id, 1000, VALID_CARD, currency="USD")
    assert exc.value.code == "UNSUPPORTED_CURRENCY"


def test_inactive_merchant(merchant):
    deactivate_merchant(merchant.id)
    with pytest.raises(InvalidStateError) as exc:
        create_payment(merchant.id, 1000, VALID_CARD)
    assert exc.value.code == "MERCHANT_INACTIVE"


def test_capture_once(merchant):
    payment = create_payment(merchant.id, 1000, VALID_CARD)
    assert capture_payment(payment.id).status == PaymentStatus.CAPTURED
    with pytest.raises(InvalidStateError) as exc:
        capture_payment(payment.id)
    assert exc.value.code == "PAYMENT_NOT_CAPTURABLE"
