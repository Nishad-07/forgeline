from __future__ import annotations

from tests.conftest import VALID_CARD


def _captured_payment(client, amount=10_000):
    merchant = client.post("/merchants", json={"name": "Chai Point"}).json()
    payment = client.post(
        "/payments", json={"merchant_id": merchant["id"], "amount_paise": amount, "card_number": VALID_CARD}
    ).json()
    return client.post(f"/payments/{payment['id']}/capture").json()


def test_payment_flow(client):
    payment = _captured_payment(client)
    assert payment["status"] == "CAPTURED"
    assert payment["card"] == "**** **** **** 1111"
    assert "card_number" not in payment


def test_refund_flow(client):
    payment = _captured_payment(client)
    resp = client.post(f"/payments/{payment['id']}/refunds", json={"reason": "damaged"})
    assert resp.status_code == 201
    assert resp.json()["amount_paise"] == 10_000
    assert client.get(f"/payments/{payment['id']}").json()["status"] == "REFUNDED"
    assert len(client.get(f"/payments/{payment['id']}/refunds").json()) == 1


def test_error_shape(client):
    resp = client.get("/payments/pay_missing")
    assert resp.status_code == 404
    assert resp.json() == {"error": "PAYMENT_NOT_FOUND", "message": "Payment pay_missing not found"}


def test_double_refund_conflict(client):
    payment = _captured_payment(client)
    client.post(f"/payments/{payment['id']}/refunds", json={})
    resp = client.post(f"/payments/{payment['id']}/refunds", json={})
    assert resp.status_code == 409
    assert resp.json()["error"] == "PAYMENT_NOT_REFUNDABLE"
