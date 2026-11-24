"""In-memory persistence. Swappable for a database in a real deployment."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.models import Merchant, Payment, Refund


class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.merchants: dict[str, Merchant] = {}
        self.payments: dict[str, Payment] = {}
        self.refunds: dict[str, Refund] = {}
        self.outbox: list[dict[str, Any]] = []


store = Store()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
