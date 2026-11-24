from __future__ import annotations

import pytest

from app.errors import NotFoundError, ValidationError
from app.merchants import create_merchant, deactivate_merchant, get_merchant


def test_create_and_get_merchant():
    merchant = create_merchant("  Chai Point ")
    assert merchant.name == "Chai Point"
    assert get_merchant(merchant.id) is merchant


def test_blank_name_rejected():
    with pytest.raises(ValidationError) as exc:
        create_merchant("   ")
    assert exc.value.code == "INVALID_MERCHANT_NAME"


def test_unknown_merchant():
    with pytest.raises(NotFoundError):
        get_merchant("mer_missing")


def test_deactivate():
    merchant = create_merchant("Dosa Corner")
    assert deactivate_merchant(merchant.id).active is False
