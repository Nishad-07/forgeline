"""Hidden golden tests for REQ-104 (void an uncaptured payment).

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


def _payment(client, capture=False):
    merchant = client.post("/merchants", json={"name": "Golden Cafe"}).json()
    payment = client.post(
        "/payments", json={"merchant_id": merchant["id"], "amount_paise": 10_000, "card_number": CARD}
    ).json()
    if capture:
        payment = client.post(f"/payments/{payment['id']}/capture").json()
    return payment


def _void(client, payment_id):
    return client.post(f"/payments/{payment_id}/void")


def _events(merchant_id, name):
    return [m for m in get_outbox(merchant_id) if m["event"] == name]


def test_void_created_payment(client):
    p = _payment(client)
    resp = _void(client, p["id"])
    assert resp.status_code == 200
    assert resp.json()["id"] == p["id"]
    assert resp.json()["status"] == "VOIDED"
    assert client.get(f"/payments/{p['id']}").json()["status"] == "VOIDED"


def test_void_emits_notification(client):
    p = _payment(client)
    _void(client, p["id"])
    events = _events(p["merchant_id"], "payment.voided")
    assert len(events) == 1
    assert events[0]["payload"]["payment_id"] == p["id"]


def test_captured_payment_cannot_be_voided(client):
    p = _payment(client, capture=True)
    resp = _void(client, p["id"])
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_VOIDABLE"
    assert client.get(f"/payments/{p['id']}").json()["status"] == "CAPTURED"
    assert _events(p["merchant_id"], "payment.voided") == []


def test_refunded_payment_cannot_be_voided(client):
    p = _payment(client, capture=True)
    client.post(f"/payments/{p['id']}/refunds", json={})
    assert client.get(f"/payments/{p['id']}").json()["status"] == "REFUNDED"
    resp = _void(client, p["id"])
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_VOIDABLE"
    assert client.get(f"/payments/{p['id']}").json()["status"] == "REFUNDED"


def test_voiding_twice_is_rejected(client):
    p = _payment(client)
    assert _void(client, p["id"]).status_code == 200
    resp = _void(client, p["id"])
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_VOIDABLE"
    assert len(_events(p["merchant_id"], "payment.voided")) == 1


def test_voided_payment_cannot_be_captured(client):
    p = _payment(client)
    _void(client, p["id"])
    resp = client.post(f"/payments/{p['id']}/capture")
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_CAPTURABLE"
    assert client.get(f"/payments/{p['id']}").json()["status"] == "VOIDED"


def test_voided_payment_cannot_be_refunded(client):
    p = _payment(client)
    _void(client, p["id"])
    resp = client.post(f"/payments/{p['id']}/refunds", json={"reason": "nope"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_REFUNDABLE"
    assert client.get(f"/payments/{p['id']}/refunds").json() == []


def test_void_unknown_payment_is_404(client):
    resp = _void(client, "pay_doesnotexist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "PAYMENT_NOT_FOUND"


def test_void_does_not_touch_other_payments(client):
    p = _payment(client)
    other = client.post(
        "/payments", json={"merchant_id": p["merchant_id"], "amount_paise": 500, "card_number": CARD}
    ).json()
    _void(client, p["id"])
    assert client.get(f"/payments/{other['id']}").json()["status"] == "CREATED"
    assert client.post(f"/payments/{other['id']}/capture").status_code == 200


def test_python_api_mirrors_http(client):
    from app.errors import InvalidStateError
    from app.payments import void_payment

    p = _payment(client)
    assert void_payment(p["id"]).status.value == "VOIDED"
    with pytest.raises(InvalidStateError) as excinfo:
        void_payment(p["id"])
    assert excinfo.value.code == "PAYMENT_NOT_VOIDABLE"
