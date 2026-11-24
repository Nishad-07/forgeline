"""Hidden golden tests for REQ-106 (daily transaction limit per merchant).

Never shown to agents. The Evaluator copies this file into the run's worktree after the
workflow finishes, to compare agent output against a human-written baseline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.notifications import get_outbox
from app.store import store

CARD = "4111111111111111"


@pytest.fixture(autouse=True)
def _reset():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    return TestClient(app)


def _merchant(client, limit=None, name="Golden Cafe"):
    body = {"name": name}
    if limit is not None:
        body["daily_limit_paise"] = limit
    return client.post("/merchants", json=body)


def _pay(client, merchant_id, amount):
    return client.post(
        "/payments", json={"merchant_id": merchant_id, "amount_paise": amount, "card_number": CARD}
    )


def _created_events(merchant_id):
    return [m for m in get_outbox(merchant_id) if m["event"] == "payment.created"]


def test_limit_is_returned_on_create_and_get(client):
    created = _merchant(client, limit=50_000)
    assert created.status_code == 201
    assert created.json()["daily_limit_paise"] == 50_000
    fetched = client.get(f"/merchants/{created.json()['id']}").json()
    assert fetched["daily_limit_paise"] == 50_000


def test_merchant_without_limit_has_null_limit_and_no_cap(client):
    created = _merchant(client).json()
    assert created["daily_limit_paise"] is None
    for _ in range(3):
        assert _pay(client, created["id"], 10_000_000).status_code == 201


@pytest.mark.parametrize("limit", [0, -1, -50_000])
def test_non_positive_limit_rejected(client, limit):
    resp = _merchant(client, limit=limit)
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_LIMIT"
    assert store.merchants == {}


def test_payment_within_limit_is_allowed(client):
    m = _merchant(client, limit=10_000).json()["id"]
    assert _pay(client, m, 4_000).status_code == 201
    assert _pay(client, m, 5_000).status_code == 201


def test_payment_exceeding_limit_is_rejected(client):
    m = _merchant(client, limit=10_000).json()["id"]
    resp = _pay(client, m, 10_001)
    assert resp.status_code == 400
    assert resp.json()["error"] == "DAILY_LIMIT_EXCEEDED"


def test_limit_is_inclusive(client):
    m = _merchant(client, limit=10_000).json()["id"]
    assert _pay(client, m, 6_000).status_code == 201
    assert _pay(client, m, 4_000).status_code == 201
    resp = _pay(client, m, 1)
    assert resp.status_code == 400
    assert resp.json()["error"] == "DAILY_LIMIT_EXCEEDED"


def test_cumulative_payments_are_checked(client):
    m = _merchant(client, limit=10_000).json()["id"]
    _pay(client, m, 7_000)
    resp = _pay(client, m, 3_001)
    assert resp.status_code == 400
    assert resp.json()["error"] == "DAILY_LIMIT_EXCEEDED"


def test_rejected_payment_creates_nothing_and_uses_no_limit(client):
    m = _merchant(client, limit=1_000).json()["id"]
    _pay(client, m, 600)
    assert _pay(client, m, 500).status_code == 400
    assert len(_created_events(m)) == 1
    # If the rejected payment had been stored, this would now be refused.
    assert _pay(client, m, 400).status_code == 201
    assert len(_created_events(m)) == 2


def test_limits_are_per_merchant(client):
    a = _merchant(client, limit=1_000, name="A").json()["id"]
    b = _merchant(client, limit=1_000, name="B").json()["id"]
    assert _pay(client, a, 1_000).status_code == 201
    assert _pay(client, a, 1).status_code == 400
    assert _pay(client, b, 1_000).status_code == 201


def test_captured_payments_still_count(client):
    m = _merchant(client, limit=2_000).json()["id"]
    payment = _pay(client, m, 1_500).json()
    client.post(f"/payments/{payment['id']}/capture")
    resp = _pay(client, m, 501)
    assert resp.status_code == 400
    assert resp.json()["error"] == "DAILY_LIMIT_EXCEEDED"


def test_python_api_mirrors_http(client):
    from app.errors import ValidationError
    from app.merchants import create_merchant
    from app.payments import create_payment

    merchant = create_merchant("Python Cafe", daily_limit_paise=3_000)
    assert merchant.daily_limit_paise == 3_000
    create_payment(merchant.id, 3_000, CARD)
    with pytest.raises(ValidationError) as excinfo:
        create_payment(merchant.id, 1, CARD)
    assert excinfo.value.code == "DAILY_LIMIT_EXCEEDED"
    with pytest.raises(ValidationError) as excinfo:
        create_merchant("Bad", daily_limit_paise=0)
    assert excinfo.value.code == "INVALID_LIMIT"
