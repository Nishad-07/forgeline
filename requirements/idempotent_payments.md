---
id: REQ-103
title: Idempotent payment creation
repository: sample_repo (PayLite)
priority: high
requested_by: Merchant Integrations
---

# Idempotent payment creation

## Problem
When a merchant's server times out while calling `POST /payments`, it cannot know whether the payment was created. Retrying blindly risks charging the customer twice, and not retrying risks losing the sale.

## Requirement
Merchants can safely retry a payment creation request. A retry that carries the same idempotency key must never create a second payment and never send a second `payment.created` notification.

## API contract
- `POST /payments` accepts an optional `idempotency_key` (string, 1 to 64 characters).
  - A key that is empty or longer than 64 characters is rejected with `400` and error code `INVALID_IDEMPOTENCY_KEY`.
  - If no key is sent, behaviour is unchanged: every call creates a new payment and returns `201`.
- Keys are scoped to the merchant: the same key may be used by different merchants without any interaction.
- First request with a key: create the payment as today and respond `201`.
- Repeat request with the same merchant, the same key and an identical `amount_paise`, `card_number` and `currency`: respond `200` with the **original** payment (same `id`). No new payment is created and no new notification is queued.
- Repeat request with the same merchant and key but a different `amount_paise`, `card_number` or `currency`: respond `409` with error code `IDEMPOTENCY_KEY_REUSED`. Nothing is created.
- The Python function `app.payments.create_payment(merchant_id, amount_paise, card_number, currency="INR", idempotency_key=None)` mirrors the API and returns the original payment on a replay.

## Not decided yet
The product team has not decided how long an idempotency key is remembered (for example 24 hours, 7 days, or forever), or whether a request that failed validation should "use up" its key.

## Out of scope
Idempotency for capture and refund calls, persistence across restarts, concurrent duplicate requests.
