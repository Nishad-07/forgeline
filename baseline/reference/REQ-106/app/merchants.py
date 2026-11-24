"""Merchant onboarding and lookup."""

from __future__ import annotations

from app.errors import NotFoundError, ValidationError
from app.models import Merchant
from app.store import new_id, store


def create_merchant(name: str, daily_limit_paise: int | None = None) -> Merchant:
    name = name.strip()
    if not name:
        raise ValidationError("Merchant name is required", code="INVALID_MERCHANT_NAME")
    if daily_limit_paise is not None and (
        isinstance(daily_limit_paise, bool)
        or not isinstance(daily_limit_paise, int)
        or daily_limit_paise <= 0
    ):
        raise ValidationError("Daily limit must be a positive integer", code="INVALID_LIMIT")
    merchant = Merchant(id=new_id("mer"), name=name, daily_limit_paise=daily_limit_paise)
    store.merchants[merchant.id] = merchant
    return merchant


def get_merchant(merchant_id: str) -> Merchant:
    merchant = store.merchants.get(merchant_id)
    if merchant is None:
        raise NotFoundError(f"Merchant {merchant_id} not found", code="MERCHANT_NOT_FOUND")
    return merchant


def deactivate_merchant(merchant_id: str) -> Merchant:
    merchant = get_merchant(merchant_id)
    merchant.active = False
    return merchant
