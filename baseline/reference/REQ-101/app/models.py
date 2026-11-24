"""Domain models. All money amounts are integers in paise (1 INR = 100 paise)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utcnow() -> datetime:
    return datetime.now(UTC)


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    CAPTURED = "CAPTURED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


@dataclass
class Merchant:
    id: str
    name: str
    active: bool = True


@dataclass
class Payment:
    id: str
    merchant_id: str
    amount_paise: int
    currency: str
    # TODO(tech-debt): replace with a vault token. Never log or return the raw card number.
    card_number: str
    status: PaymentStatus = PaymentStatus.CREATED
    created_at: datetime = field(default_factory=utcnow)
    captured_at: datetime | None = None
    refunded_paise: int = 0

    @property
    def refundable_paise(self) -> int:
        return self.amount_paise - self.refunded_paise

    @property
    def masked_card(self) -> str:
        return "**** **** **** " + self.card_number[-4:]


@dataclass
class Refund:
    id: str
    payment_id: str
    amount_paise: int
    reason: str
    created_at: datetime = field(default_factory=utcnow)
