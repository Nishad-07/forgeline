"""Hidden golden tests for REQ-103 (idempotent payment creation).

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
OTHER_CARD = "5555555555554444"


@pytest.fixture(autouse=True)
def _reset():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    return TestClient(app)


def _merchant(client, name="Golden Cafe"):
    return client.post("/merchants", json={"name": name}).json()["id"]


def _pay(client, merchant_id, key=None, amount=10_000, card=CARD, currency="INR"):
    body = {"merchant_id": merchant_id, "amount_paise": amount, "card_number": card, "currency": currency}
    if key is not None:
        body["idempotency_key"] = key
    return client.post("/payments", json=body)


def _created_events(merchant_id):
    return [m for m in get_outbox(merchant_id) if m["event"] == "payment.created"]


def test_first_request_with_key_creates_payment(client):
    m = _merchant(client)
    resp = _pay(client, m, key="order-1")
    assert resp.status_code == 201
    assert resp.json()["status"] == "CREATED"


def test_replay_returns_original_payment(client):
    m = _merchant(client)
    first = _pay(client, m, key="order-1").json()
    resp = _pay(client, m, key="order-1")
    assert resp.status_code == 200
    assert resp.json() == first


def test_replay_creates_no_second_payment_or_notification(client):
    m = _merchant(client)
    _pay(client, m, key="order-1")
    _pay(client, m, key="order-1")
    _pay(client, m, key="order-1")
    assert len(_created_events(m)) == 1


def test_replay_after_capture_still_returns_original_payment(client):
    m = _merchant(client)
    first = _pay(client, m, key="order-1").json()
    client.post(f"/payments/{first['id']}/capture")
    resp = _pay(client, m, key="order-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == first["id"]
    assert len(_created_events(m)) == 1


@pytest.mark.parametrize(
    "changes",
    [{"amount": 10_001}, {"card": OTHER_CARD}],
    ids=["amount", "card"],
)
def test_same_key_with_different_body_conflicts(client, changes):
    m = _merchant(client)
    first = _pay(client, m, key="order-1").json()
    resp = _pay(client, m, key="order-1", **changes)
    assert resp.status_code == 409
    assert resp.json()["error"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(_created_events(m)) == 1
    assert client.get(f"/payments/{first['id']}").json() == first


def test_same_key_with_different_currency_conflicts(client):
    m = _merchant(client)
    _pay(client, m, key="order-1")
    resp = _pay(client, m, key="order-1", currency="USD")
    # Whichever check fires first, the key must never produce a second payment.
    assert resp.status_code in (400, 409)
    assert len(_created_events(m)) == 1


def test_different_merchants_may_reuse_the_same_key(client):
    a, b = _merchant(client, "A"), _merchant(client, "B")
    first = _pay(client, a, key="shared")
    second = _pay(client, b, key="shared")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_different_keys_create_different_payments(client):
    m = _merchant(client)
    one = _pay(client, m, key="k1").json()
    two = _pay(client, m, key="k2").json()
    assert one["id"] != two["id"]
    assert len(_created_events(m)) == 2


def test_requests_without_key_keep_old_behaviour(client):
    m = _merchant(client)
    one, two = _pay(client, m), _pay(client, m)
    assert one.status_code == 201
    assert two.status_code == 201
    assert one.json()["id"] != two.json()["id"]
    assert len(_created_events(m)) == 2


def test_key_length_boundaries(client):
    m = _merchant(client)
    assert _pay(client, m, key="k" * 64).status_code == 201
    too_long = _pay(client, m, key="k" * 65)
    assert too_long.status_code == 400
    assert too_long.json()["error"] == "INVALID_IDEMPOTENCY_KEY"
    empty = _pay(client, m, key="")
    assert empty.status_code == 400
    assert empty.json()["error"] == "INVALID_IDEMPOTENCY_KEY"
    assert len(_created_events(m)) == 1


def test_python_api_returns_original_payment(client):
    from app.payments import create_payment

    m = _merchant(client)
    first = create_payment(m, 5_000, CARD, idempotency_key="py-1")
    again = create_payment(m, 5_000, CARD, idempotency_key="py-1")
    assert again.id == first.id
    assert len(_created_events(m)) == 1
