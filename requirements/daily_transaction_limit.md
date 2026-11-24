---
id: REQ-106
title: Daily transaction limit per merchant
repository: sample_repo (PayLite)
priority: high
requested_by: Risk and Compliance
---

# Daily transaction limit per merchant

## Problem
Newly onboarded merchants are a fraud and money-laundering risk. Compliance wants to cap how much a merchant can process in a single day, so that a compromised or fraudulent merchant account cannot move unlimited money before it is noticed.

## Requirement
A merchant can be configured with a daily limit. Once the day's payments would exceed the limit, new payments for that merchant are refused until the next UTC day. The limit is a risk control: it must not be possible to get around it by splitting a payment into several requests.

## API contract
- `POST /merchants` accepts an optional `daily_limit_paise` (integer).
  - If omitted, the merchant has no limit (today's behaviour) and `daily_limit_paise` is `null`.
  - It must be a positive integer, otherwise respond `400` with error code `INVALID_LIMIT` and create nothing.
- `POST /merchants` and `GET /merchants/{merchant_id}` return `daily_limit_paise` (integer or `null`).
- Creating a payment is refused with `400` and error code `DAILY_LIMIT_EXCEEDED` when the sum of the merchant's payment amounts created today (UTC), plus the new amount, is greater than the merchant's `daily_limit_paise`. No payment and no notification is created.
  - The limit is inclusive: a payment that brings the total exactly to the limit is allowed.
  - Only payments of that merchant count; other merchants have independent totals.
  - Payments that have been voided do not count towards the limit.
  - A merchant without a limit is never refused for this reason.
- The Python function `app.merchants.create_merchant(name, daily_limit_paise=None)` mirrors the API.

## Not decided yet
The product team has not decided whether refunded amounts should free up the daily limit again, or whether the "day" should follow the merchant's local time zone instead of UTC.

## Out of scope
Changing a merchant's limit after creation, monthly limits, per-card limits, alerts to the compliance team.
