---
id: REQ-104
title: Void an uncaptured payment
repository: sample_repo (PayLite)
priority: medium
requested_by: Merchant Success
---

# Void an uncaptured payment

## Problem
A customer abandons an order after the payment was created but before it was captured. Today the payment stays in `CREATED` forever and could still be captured by mistake. Merchants want to cancel it cleanly instead of refunding after the fact.

## Requirement
A merchant can void a payment that has not been captured yet. A voided payment is final: it can never be captured or refunded.

## API contract
- `POST /payments/{payment_id}/void` voids the payment and returns the payment (`200`) with status `VOIDED`.
  - Only payments in status `CREATED` can be voided.
  - A payment that is already `CAPTURED`, `PARTIALLY_REFUNDED`, `REFUNDED` or `VOIDED` cannot be voided: respond `409` with error code `PAYMENT_NOT_VOIDABLE` and change nothing.
  - Unknown payment id: `404`, `PAYMENT_NOT_FOUND`.
- A voided payment cannot be captured: `409`, `PAYMENT_NOT_CAPTURABLE`.
- A voided payment cannot be refunded: `409`, `PAYMENT_NOT_REFUNDABLE`.
- Voiding queues a `payment.voided` notification for the merchant with payload `{"payment_id": ...}`. A rejected void queues nothing.
- The Python function `app.payments.void_payment(payment_id)` mirrors the API.

## Not decided yet
The product team has not decided whether a voided payment should still be visible in merchant-facing lists and reports, or whether a void should require a reason.

## Out of scope
Voiding captured payments (that is a refund), automatic voiding of stale payments, releasing a hold at the card network.
