"""Merchant notifications (webhook outbox). Events are queued; a worker would deliver them."""

from __future__ import annotations

from typing import Any

from app.store import store


def notify(merchant_id: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    message = {"merchant_id": merchant_id, "event": event, "payload": dict(payload)}
    store.outbox.append(message)
    return message


def get_outbox(merchant_id: str) -> list[dict[str, Any]]:
    return [m for m in store.outbox if m["merchant_id"] == merchant_id]
