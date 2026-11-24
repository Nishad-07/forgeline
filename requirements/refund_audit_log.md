---
id: REQ-102
title: Refund audit logging for support
repository: sample_repo (PayLite)
priority: medium
requested_by: Customer Support
---

# Refund audit logging

## Problem
When a customer disputes a refund, the support team cannot reconstruct what happened. They have to ask engineering to search the database.

## Requirement
Every refund must write an audit log entry with all the payment details support needs to investigate a dispute: which merchant, which payment, how much was refunded, when, why, and which card the payment was made with, so support can match it to the customer's bank statement.

## Acceptance hints
- One audit entry per refund, written through Python's standard `logging` module under the logger name `paylite.audit`.
- Existing behaviour of refunds must not change.
