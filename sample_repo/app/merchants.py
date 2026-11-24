"""Merchant onboarding and lookup."""

from __future__ import annotations

from app.errors import NotFoundError, ValidationError
from app.models import Merchant
from app.store import new_id, store


def create_merchant(name: str) -> Merchant:
    name = name.strip()
    if not name:
        raise ValidationError("Merchant name is required", code="INVALID_MERCHANT_NAME")
    merchant = Merchant(id=new_id("mer"), name=name)
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
