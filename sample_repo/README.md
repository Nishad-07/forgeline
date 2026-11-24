# PayLite (sample target repository)

A deliberately small payments service that the Forgeline agents work on. It is copied into `workspace/` and turned into its own git repository at run time, so agent changes never touch this source copy.

| Module | Responsibility |
|---|---|
| `app/models.py` | Merchant, Payment, Refund dataclasses; amounts in integer paise |
| `app/store.py` | In-memory store |
| `app/errors.py` | Domain errors with stable codes and HTTP status |
| `app/merchants.py` | Merchant onboarding |
| `app/payments.py` | Create and capture payments (Luhn check, limits) |
| `app/refunds.py` | Refunds (**full refunds only** today) |
| `app/notifications.py` | Merchant webhook outbox |
| `app/main.py` | FastAPI routes |

```powershell
pytest
```

Known tech debt: `Payment.card_number` stores the raw card number. It must never be logged or returned by the API.
