# Baseline (hidden from agents)

Used by the Evaluator to compare agent-generated output with a human-written baseline.

| Path | Purpose |
|---|---|
| `golden_tests/` | Tests written by a human from the requirement's contract. Copied into the run worktree **after** the workflow finishes; agents never see them. |
| `reference/REQ-101/` | Human reference implementation of partial refunds (files overlaid on `sample_repo`). Proves the golden tests are passable and gives a size/scope baseline for the diff. |
| `reference/REQ-102/` | Secure reference implementation of refund audit logging (masked card only). |
| `reference/REQ-103/` .. `REQ-106/` | Reference implementations of idempotent payment creation, voiding, merchant payment listing and the daily transaction limit. |

`manifest.yaml` maps every requirement id to its golden tests and reference directory; `scripts/verify_baseline.py` iterates over it.

The policy marks `baseline/**` and `tests/golden/**` as protected paths, so no agent can read or modify them.
