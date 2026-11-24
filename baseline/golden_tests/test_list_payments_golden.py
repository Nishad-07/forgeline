"""Hidden golden tests for REQ-105 (list a merchant's payments).

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


def _merchant(client, name="Golden Cafe"):
    return client.post("/merchants", json={"name": name}).json()["id"]


def _pay(client, merchant_id, amount=1_000, capture=False):
    payment = client.post(
        "/payments", json={"merchant_id": merchant_id, "amount_paise": amount, "card_number": CARD}
    ).json()
    if capture:
        payment = client.post(f"/payments/{payment['id']}/capture").json()
    return payment


def _list(client, merchant_id, **params):
    return client.get(f"/merchants/{merchant_id}/payments", params=params)


def test_empty_list_for_new_merchant(client):
    m = _merchant(client)
    resp = _list(client, m)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_items_are_newest_first_with_payment_shape(client):
    m = _merchant(client)
    made = [_pay(client, m, amount=100 * (i + 1)) for i in range(3)]
    body = _list(client, m).json()
    assert [p["id"] for p in body["items"]] == [p["id"] for p in reversed(made)]
    assert body["total"] == 3
    assert body["items"][0] == client.get(f"/payments/{made[2]['id']}").json()


def test_other_merchants_payments_never_appear(client):
    a, b = _merchant(client, "A"), _merchant(client, "B")
    mine = _pay(client, a)
    _pay(client, b)
    _pay(client, b)
    body = _list(client, a).json()
    assert [p["id"] for p in body["items"]] == [mine["id"]]
    assert body["total"] == 1
    assert all(p["merchant_id"] == a for p in body["items"])


def test_card_is_masked_and_raw_number_never_leaks(client):
    m = _merchant(client)
    _pay(client, m)
    resp = _list(client, m)
    assert CARD not in resp.text
    assert resp.json()["items"][0]["card"] == "**** **** **** 1111"
    assert "card_number" not in resp.json()["items"][0]


def test_status_filter(client):
    m = _merchant(client)
    created = _pay(client, m)
    captured = _pay(client, m, capture=True)
    body = _list(client, m, status="CAPTURED").json()
    assert [p["id"] for p in body["items"]] == [captured["id"]]
    assert body["total"] == 1
    body = _list(client, m, status="CREATED").json()
    assert [p["id"] for p in body["items"]] == [created["id"]]


def test_status_filter_with_no_matches_is_empty(client):
    m = _merchant(client)
    _pay(client, m)
    body = _list(client, m, status="REFUNDED").json()
    assert body["items"] == []
    assert body["total"] == 0


def test_invalid_status_rejected(client):
    m = _merchant(client)
    resp = _list(client, m, status="BOGUS")
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_STATUS"


def test_pagination_limit_and_offset(client):
    m = _merchant(client)
    made = [_pay(client, m, amount=100 + i) for i in range(5)]
    newest_first = [p["id"] for p in reversed(made)]
    page = _list(client, m, limit=2, offset=1).json()
    assert [p["id"] for p in page["items"]] == newest_first[1:3]
    assert page["total"] == 5
    assert page["limit"] == 2
    assert page["offset"] == 1
    last = _list(client, m, limit=2, offset=4).json()
    assert [p["id"] for p in last["items"]] == newest_first[4:]
    beyond = _list(client, m, offset=50).json()
    assert beyond["items"] == []
    assert beyond["total"] == 5


def test_default_limit_is_20_and_100_is_allowed(client):
    m = _merchant(client)
    for _ in range(25):
        _pay(client, m)
    body = _list(client, m).json()
    assert len(body["items"]) == 20
    assert body["total"] == 25
    assert body["limit"] == 20
    assert _list(client, m, limit=100).status_code == 200


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "abc"}]
)
def test_invalid_pagination_rejected(client, params):
    m = _merchant(client)
    resp = _list(client, m, **params)
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_PAGINATION"


def test_unknown_merchant_is_404(client):
    resp = _list(client, "mer_doesnotexist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "MERCHANT_NOT_FOUND"
