---
id: REQ-105
title: List a merchant's payments
repository: sample_repo (PayLite)
priority: medium
requested_by: Merchant Dashboard team
---

# List a merchant's payments

## Problem
The merchant dashboard can only look up a payment if it already knows the payment id. Merchants cannot see their recent transactions or find the ones still waiting to be captured.

## Requirement
A merchant's payments can be listed, newest first, optionally filtered by status, with simple pagination. A merchant must only ever see its own payments, and the response must expose the same masked card information as the existing payment lookup.

## API contract
- `GET /merchants/{merchant_id}/payments` returns `200` with:
  `{"items": [...], "total": <int>, "limit": <int>, "offset": <int>}`
  - each item has exactly the shape returned by `GET /payments/{payment_id}` (the card is shown masked only; the full card number is never returned).
  - items are ordered newest first.
  - `total` is the number of payments matching the filter, before pagination is applied.
- Optional query parameters:
  - `status`: only payments in this status. An unknown value gives `400`, `INVALID_STATUS`.
  - `limit`: page size, default `20`, allowed range 1 to 100. Anything else (including non-numeric values) gives `400`, `INVALID_PAGINATION`.
  - `offset`: number of payments to skip, default `0`, must be 0 or more, otherwise `400`, `INVALID_PAGINATION`.
- Unknown merchant: `404`, `MERCHANT_NOT_FOUND`.
- A merchant with no payments gets an empty `items` list and `total` 0.
- Payments of other merchants never appear, in `items` or in `total`.
- The Python function `app.payments.list_payments(merchant_id, status=None, limit=20, offset=0)` returns `(payments, total)`.

## Not decided yet
The product team has not decided whether the list should also support a date range filter, or whether it should include the refunded amount for each payment.

## Out of scope
Sorting by other fields, cursor-based pagination, exporting to CSV, searching by card.
