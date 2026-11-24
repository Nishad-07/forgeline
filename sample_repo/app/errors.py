"""Domain errors. Each carries a stable machine-readable code and an HTTP status."""

from __future__ import annotations


class PaymentError(Exception):
    status_code = 400
    code = "PAYMENT_ERROR"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFoundError(PaymentError):
    status_code = 404
    code = "NOT_FOUND"


class InvalidStateError(PaymentError):
    status_code = 409
    code = "INVALID_STATE"


class ValidationError(PaymentError):
    status_code = 400
    code = "VALIDATION_ERROR"
