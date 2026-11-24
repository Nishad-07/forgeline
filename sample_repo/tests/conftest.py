from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.merchants import create_merchant
from app.payments import capture_payment, create_payment
from app.store import store

VALID_CARD = "4111111111111111"


@pytest.fixture(autouse=True)
def clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def merchant():
    return create_merchant("Chai Point")


@pytest.fixture
def captured_payment(merchant):
    payment = create_payment(merchant.id, 10_000, VALID_CARD)
    return capture_payment(payment.id)
