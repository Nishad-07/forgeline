"""Refunds: full or partial refunds of captured payments."""

from __future__ import annotations

import logging

from app.errors import InvalidStateError, ValidationError
from app.models import PaymentStatus, Refund
from app.notifications import notify
from app.payments import get_payment
from app.store import new_id, store

logger = logging.getLogger("paylite.refunds")

REFUNDABLE_STATUSES = {PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED}


def refund_payment(payment_id: str, amount_paise: int | None = None, reason: str = "") -> Refund:
    """Refund part or all of a captured payment. Omitting the amount refunds the remaining balance."""
    payment = get_payment(payment_id)
    if payment.status not in REFUNDABLE_STATUSES:
        raise InvalidStateError(
            f"Payment in status {payment.status.value} cannot be refunded", code="PAYMENT_NOT_REFUNDABLE"
        )

    remaining = payment.refundable_paise
    if amount_paise is None:
        amount_paise = remaining
    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int) or amount_paise <= 0:
        raise ValidationError("Refund amount must be a positive integer", code="INVALID_AMOUNT")
    if amount_paise > remaining:
        raise ValidationError(
            f"Refund of {amount_paise} exceeds remaining refundable amount {remaining}",
            code="REFUND_EXCEEDS_REMAINING",
        )

    refund = Refund(id=new_id("rfnd"), payment_id=payment.id, amount_paise=amount_paise, reason=reason)
    store.refunds[refund.id] = refund
    payment.refunded_paise += amount_paise
    payment.status = (
        PaymentStatus.REFUNDED if payment.refundable_paise == 0 else PaymentStatus.PARTIALLY_REFUNDED
    )
    logger.info("refund issued id=%s payment=%s amount=%s", refund.id, payment.id, refund.amount_paise)
    notify(
        payment.merchant_id,
        "refund.created",
        {"refund_id": refund.id, "payment_id": payment.id, "amount_paise": refund.amount_paise},
    )
    return refund


def list_refunds(payment_id: str) -> list[Refund]:
    get_payment(payment_id)  # raises if the payment does not exist
    refunds = [r for r in store.refunds.values() if r.payment_id == payment_id]
    return sorted(refunds, key=lambda r: r.created_at)
