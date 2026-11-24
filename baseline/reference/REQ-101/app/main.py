"""HTTP API for PayLite."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.errors import PaymentError
from app.merchants import create_merchant, get_merchant
from app.models import Payment, Refund
from app.payments import capture_payment, create_payment, get_payment
from app.refunds import list_refunds, refund_payment

app = FastAPI(title="PayLite", version="1.0.0")


class MerchantIn(BaseModel):
    name: str = Field(min_length=1)


class PaymentIn(BaseModel):
    merchant_id: str
    amount_paise: int
    card_number: str
    currency: str = "INR"


class RefundIn(BaseModel):
    amount_paise: int | None = None
    reason: str = ""


def payment_out(payment: Payment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "merchant_id": payment.merchant_id,
        "amount_paise": payment.amount_paise,
        "currency": payment.currency,
        "status": payment.status.value,
        "refunded_paise": payment.refunded_paise,
        "refundable_paise": payment.refundable_paise,
        "card": payment.masked_card,
    }


def refund_out(refund: Refund) -> dict[str, Any]:
    return {
        "id": refund.id,
        "payment_id": refund.payment_id,
        "amount_paise": refund.amount_paise,
        "reason": refund.reason,
        "created_at": refund.created_at.isoformat(),
    }


@app.exception_handler(PaymentError)
async def payment_error_handler(_: Request, exc: PaymentError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "message": exc.message})


@app.post("/merchants", status_code=201)
def post_merchant(body: MerchantIn) -> dict[str, Any]:
    merchant = create_merchant(body.name)
    return {"id": merchant.id, "name": merchant.name, "active": merchant.active}


@app.get("/merchants/{merchant_id}")
def read_merchant(merchant_id: str) -> dict[str, Any]:
    merchant = get_merchant(merchant_id)
    return {"id": merchant.id, "name": merchant.name, "active": merchant.active}


@app.post("/payments", status_code=201)
def post_payment(body: PaymentIn) -> dict[str, Any]:
    return payment_out(create_payment(body.merchant_id, body.amount_paise, body.card_number, body.currency))


@app.get("/payments/{payment_id}")
def read_payment(payment_id: str) -> dict[str, Any]:
    return payment_out(get_payment(payment_id))


@app.post("/payments/{payment_id}/capture")
def post_capture(payment_id: str) -> dict[str, Any]:
    return payment_out(capture_payment(payment_id))


@app.post("/payments/{payment_id}/refunds", status_code=201)
def post_refund(payment_id: str, body: RefundIn) -> dict[str, Any]:
    return refund_out(refund_payment(payment_id, amount_paise=body.amount_paise, reason=body.reason))


@app.get("/payments/{payment_id}/refunds")
def read_refunds(payment_id: str) -> list[dict[str, Any]]:
    return [refund_out(r) for r in list_refunds(payment_id)]
