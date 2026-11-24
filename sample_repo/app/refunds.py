"""Refunds. Currently only full refunds of captured payments are supported."""

from __future__ import annotations

import logging

from app.errors import InvalidStateError
from app.models import PaymentStatus, Refund
from app.notifications import notify
from app.payments import get_payment
from app.store import new_id, store

logger = logging.getLogger("paylite.refunds")


def refund_payment(payment_id: str, reason: str = "") -> Refund:
    """Refund the full captured amount of a payment."""
    payment = get_payment(payment_id)
    if payment.status != PaymentStatus.CAPTURED:
        raise InvalidStateError(
            f"Payment in status {payment.status.value} cannot be refunded", code="PAYMENT_NOT_REFUNDABLE"
        )

    refund = Refund(
        id=new_id("rfnd"), payment_id=payment.id, amount_paise=payment.amount_paise, reason=reason
    )
    store.refunds[refund.id] = refund
    payment.status = PaymentStatus.REFUNDED
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
