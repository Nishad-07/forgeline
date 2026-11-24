"""Payment creation and capture."""

from __future__ import annotations

import logging

from app.errors import InvalidStateError, NotFoundError, ValidationError
from app.merchants import get_merchant
from app.models import Payment, PaymentStatus, utcnow
from app.notifications import notify
from app.store import new_id, store

logger = logging.getLogger("paylite.payments")

SUPPORTED_CURRENCIES = {"INR"}
MAX_AMOUNT_PAISE = 10_000_000  # 1,00,000 INR per transaction
MAX_PAGE_SIZE = 100


def luhn_valid(card_number: str) -> bool:
    digits = [int(d) for d in card_number]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def create_payment(merchant_id: str, amount_paise: int, card_number: str, currency: str = "INR") -> Payment:
    merchant = get_merchant(merchant_id)
    if not merchant.active:
        raise InvalidStateError("Merchant is inactive", code="MERCHANT_INACTIVE")
    if not isinstance(amount_paise, int) or amount_paise <= 0 or amount_paise > MAX_AMOUNT_PAISE:
        raise ValidationError("Amount must be between 1 and 10000000 paise", code="INVALID_AMOUNT")
    if currency not in SUPPORTED_CURRENCIES:
        raise ValidationError(f"Unsupported currency {currency}", code="UNSUPPORTED_CURRENCY")
    if not card_number.isdigit() or not 12 <= len(card_number) <= 19 or not luhn_valid(card_number):
        raise ValidationError("Invalid card number", code="INVALID_CARD")

    payment = Payment(
        id=new_id("pay"),
        merchant_id=merchant_id,
        amount_paise=amount_paise,
        currency=currency,
        card_number=card_number,
    )
    store.payments[payment.id] = payment
    logger.info("payment created id=%s merchant=%s amount=%s", payment.id, merchant_id, amount_paise)
    notify(merchant_id, "payment.created", {"payment_id": payment.id, "amount_paise": amount_paise})
    return payment


def get_payment(payment_id: str) -> Payment:
    payment = store.payments.get(payment_id)
    if payment is None:
        raise NotFoundError(f"Payment {payment_id} not found", code="PAYMENT_NOT_FOUND")
    return payment


def list_payments(
    merchant_id: str, status: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[Payment], int]:
    """Return one page of a merchant's payments (newest first) and the total after filtering."""
    get_merchant(merchant_id)  # raises if the merchant does not exist
    if status is not None and status not in {s.value for s in PaymentStatus}:
        raise ValidationError(f"Unknown status {status}", code="INVALID_STATUS")
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise ValidationError("limit must be 1..100 and offset >= 0", code="INVALID_PAGINATION")
    payments = [
        p
        for p in reversed(list(store.payments.values()))
        if p.merchant_id == merchant_id and (status is None or p.status.value == status)
    ]
    return payments[offset : offset + limit], len(payments)


def capture_payment(payment_id: str) -> Payment:
    payment = get_payment(payment_id)
    if payment.status != PaymentStatus.CREATED:
        raise InvalidStateError(
            f"Payment in status {payment.status.value} cannot be captured", code="PAYMENT_NOT_CAPTURABLE"
        )
    payment.status = PaymentStatus.CAPTURED
    payment.captured_at = utcnow()
    logger.info("payment captured id=%s", payment.id)
    notify(payment.merchant_id, "payment.captured", {"payment_id": payment.id})
    return payment
