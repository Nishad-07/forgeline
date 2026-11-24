# Demo requirements

Six requirements for the PayLite sample repo (`sample_repo/`). Each has a hidden golden test file and a human reference
implementation under `baseline/` (see `baseline/README.md`).

| Id | File | Title | What it demonstrates |
|---|---|---|---|
| REQ-101 | `partial_refund.md` | Partial refunds | Clarification question (should merchants be notified for every partial refund? is a reason mandatory?); API and data-model change; cumulative money rules |
| REQ-102 | `refund_audit_log.md` | Refund audit logging for support | Security path: the obvious implementation logs the raw card number; the secure one logs the masked card only |
| REQ-103 | `idempotent_payments.md` | Idempotent payment creation | API change with replay semantics (200 vs 201, 409 conflict); open question on how long keys are remembered |
| REQ-104 | `void_payment.md` | Void an uncaptured payment | State-machine change: new status, new endpoint, and impact on existing capture/refund guards; new notification event |
| REQ-105 | `list_merchant_payments.md` | List a merchant's payments | Read-only API with filtering and pagination; data isolation between merchants and no leaking of the raw card number |
| REQ-106 | `daily_transaction_limit.md` | Daily transaction limit per merchant | Risk/compliance rule: inclusive boundary, per-merchant totals, nothing created on rejection; open question on whether refunds free up the limit |
