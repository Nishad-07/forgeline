---
id: REQ-101
title: Partial refunds
repository: sample_repo (PayLite)
priority: high
requested_by: Merchant Success
---

# Partial refunds

## Problem
Merchants can only refund the full amount of a payment. A café that over-charged a customer by ₹40 on a ₹100 order has to refund everything and ask the customer to pay again. Merchants want to refund only part of a captured payment, possibly several times.

## Requirement
A merchant can issue one or more partial refunds against a captured payment. The total refunded across all refunds must never exceed the captured amount. The full history of refunds for a payment must remain available.

## API contract
- `POST /payments/{payment_id}/refunds` accepts an optional `amount_paise` (integer) next to the existing `reason`.
  - If `amount_paise` is omitted, refund the **entire remaining refundable amount** (keeps today's behaviour for existing clients).
  - `amount_paise` must be a positive integer, otherwise respond `400` with error code `INVALID_AMOUNT`.
  - If `amount_paise` is greater than the remaining refundable amount, respond `400` with error code `REFUND_EXCEEDS_REMAINING` and change nothing.
- `GET /payments/{payment_id}` additionally returns `refunded_paise` (total refunded so far) and `refundable_paise` (amount still refundable).
- Payment status:
  - `PARTIALLY_REFUNDED` after a refund that leaves some amount refundable.
  - `REFUNDED` once the total refunded equals the captured amount. No further refunds are allowed (`409`, `PAYMENT_NOT_REFUNDABLE`).
- Refunds remain impossible on payments that are not captured (`409`, `PAYMENT_NOT_REFUNDABLE`).
- The Python function `app.refunds.refund_payment(payment_id, amount_paise=None, reason="")` mirrors the API.

## Not decided yet
The product team has not decided whether merchants must be notified for every partial refund or only when the payment becomes fully refunded, or whether a refund reason should be mandatory.

## Out of scope
Multi-currency, refunds to a different card, asynchronous settlement with the card network.
