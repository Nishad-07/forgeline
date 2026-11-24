"""Hidden golden tests for REQ-101 (partial refunds).

Never shown to agents. The Evaluator copies this file into the run's worktree after the
workflow finishes, to compare agent output against a human-written baseline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
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


def _payment(client, amount=10_000, capture=True):
    merchant = client.post("/merchants", json={"name": "Golden Cafe"}).json()
    payment = client.post(
        "/payments", json={"merchant_id": merchant["id"], "amount_paise": amount, "card_number": CARD}
    ).json()
    if capture:
        payment = client.post(f"/payments/{payment['id']}/capture").json()
    return payment


def _refund(client, payment_id, **body):
    return client.post(f"/payments/{payment_id}/refunds", json=body)


def test_single_partial_refund_updates_balances(client):
    p = _payment(client)
    resp = _refund(client, p["id"], amount_paise=4_000, reason="overcharged")
    assert resp.status_code == 201
    assert resp.json()["amount_paise"] == 4_000
    state = client.get(f"/payments/{p['id']}").json()
    assert state["status"] == "PARTIALLY_REFUNDED"
    assert state["refunded_paise"] == 4_000
    assert state["refundable_paise"] == 6_000


def test_multiple_partial_refunds_accumulate(client):
    p = _payment(client)
    assert _refund(client, p["id"], amount_paise=3_000).status_code == 201
    assert _refund(client, p["id"], amount_paise=2_500).status_code == 201
    state = client.get(f"/payments/{p['id']}").json()
    assert state["refunded_paise"] == 5_500
    assert state["refundable_paise"] == 4_500


def test_cumulative_over_refund_rejected_without_side_effects(client):
    p = _payment(client)
    _refund(client, p["id"], amount_paise=7_000)
    resp = _refund(client, p["id"], amount_paise=3_001)
    assert resp.status_code == 400
    assert resp.json()["error"] == "REFUND_EXCEEDS_REMAINING"
    state = client.get(f"/payments/{p['id']}").json()
    assert state["refunded_paise"] == 7_000
    assert state["status"] == "PARTIALLY_REFUNDED"
    assert len(client.get(f"/payments/{p['id']}/refunds").json()) == 1


def test_single_refund_above_captured_rejected(client):
    p = _payment(client)
    resp = _refund(client, p["id"], amount_paise=10_001)
    assert resp.status_code == 400
    assert resp.json()["error"] == "REFUND_EXCEEDS_REMAINING"


def test_refunding_exact_remaining_marks_fully_refunded(client):
    p = _payment(client)
    _refund(client, p["id"], amount_paise=6_000)
    assert _refund(client, p["id"], amount_paise=4_000).status_code == 201
    state = client.get(f"/payments/{p['id']}").json()
    assert state["status"] == "REFUNDED"
    assert state["refundable_paise"] == 0
    resp = _refund(client, p["id"], amount_paise=1)
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_REFUNDABLE"


def test_omitted_amount_refunds_remaining_balance(client):
    p = _payment(client)
    _refund(client, p["id"], amount_paise=2_500)
    resp = _refund(client, p["id"], reason="close out")
    assert resp.status_code == 201
    assert resp.json()["amount_paise"] == 7_500
    assert client.get(f"/payments/{p['id']}").json()["status"] == "REFUNDED"


def test_full_refund_without_amount_still_works(client):
    p = _payment(client)
    resp = _refund(client, p["id"])
    assert resp.status_code == 201
    assert resp.json()["amount_paise"] == 10_000
    assert client.get(f"/payments/{p['id']}").json()["status"] == "REFUNDED"


@pytest.mark.parametrize("amount", [0, -500])
def test_non_positive_amount_rejected(client, amount):
    p = _payment(client)
    resp = _refund(client, p["id"], amount_paise=amount)
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_AMOUNT"
    assert client.get(f"/payments/{p['id']}").json()["refunded_paise"] == 0


def test_uncaptured_payment_cannot_be_partially_refunded(client):
    p = _payment(client, capture=False)
    resp = _refund(client, p["id"], amount_paise=1_000)
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_REFUNDABLE"


def test_refund_history_is_complete_and_consistent(client):
    p = _payment(client)
    for amount in (1_000, 2_000, 3_000):
        _refund(client, p["id"], amount_paise=amount)
    history = client.get(f"/payments/{p['id']}/refunds").json()
    assert [r["amount_paise"] for r in history] == [1_000, 2_000, 3_000]
    refunded = client.get(f"/payments/{p['id']}").json()["refunded_paise"]
    assert sum(r["amount_paise"] for r in history) == refunded


def test_python_api_mirrors_http(client):
    from app.refunds import refund_payment

    p = _payment(client)
    refund = refund_payment(p["id"], amount_paise=1_234, reason="python")
    assert refund.amount_paise == 1_234
    assert client.get(f"/payments/{p['id']}").json()["refundable_paise"] == 8_766
