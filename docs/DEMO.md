# Demo guide

Shows one requirement typed live moving through planning, tests-first QA, code change, test execution
(including a failed check and the recovery), security review, evaluation and the final human approval, with every
agent's output streaming in real time. Target length: 7-9 minutes.

## 0. Setup (once, before recording)

```powershell
.\.venv\Scripts\forgeline.exe sample init --reset      # clean target repository and empty history
.\.venv\Scripts\forgeline.exe doctor                   # every check green
cd dashboard; npm install; npm run build; cd ..
.\.venv\Scripts\forgeline.exe serve                    # open http://127.0.0.1:8000
```

* Provider: **Gemini** for the main demo (fast). Ollama is the automatic backup.
* Cache is on by default: do one rehearsal of the exact requirement text below; the recorded run then replays
  the same model answers instantly and spends no extra free-tier quota. Use a new wording (or `--no-cache`) when
  you want a fresh live run.

## 1. Introduce the setting (30 s)
* Open `sample_repo/app/refunds.py`: today only full refunds are possible.
* Open `policy.yaml`: retry limits, protected files, decisions reserved for humans.

## 2. Type the requirement (Agent Console) (30 s)
Paste into the requirement box, choose **Gemini**, press **Run**:

```
Partial refunds
Merchants must be able to refund part of a captured payment, possibly several times.
The total refunded must never exceed the captured amount.
POST /payments/{id}/refunds accepts an optional amount_paise; without it the remaining amount is refunded.
Invalid amounts return 400 INVALID_AMOUNT; exceeding the remaining amount returns 400 REFUND_EXCEEDS_REMAINING.
GET /payments/{id} returns refunded_paise and refundable_paise.
Status becomes PARTIALLY_REFUNDED, then REFUNDED when fully refunded.
```

## 3. Watch the agents (2 min)
* **Analyst** streams its answer, then shows Given/When/Then criteria and tasks.
* **Human gate: clarification.** Answer, e.g. "Notify the merchant on every refund. Reason is optional."
* **Architect** shows the files it allows to change and the untouched components.
* **QA** streams the acceptance tests and shows they fail on the old code (expected, tests-first).
* **Developer** streams SEARCH/REPLACE edits; the card shows before/after blocks.

## 4. The failed check and recovery (1 min)
* **Scope guard** confirms the change stays inside the approved files.
* **Tests**: in our recorded runs attempt 1 failed 2 tests; the failure messages went back to the Developer and
  attempt 2 passed all tests. Point at the attempt counter on the progress bar.
  (A live model may pass first time; the failure scenarios are also proven by the automated tests, step 8.)

## 5. Security, evaluation, release (1 min)
* **Security**: diff-aware scan + model triage, no blocking finding.
* **Evaluation**: hidden human-written tests (e.g. 12/12) and rubric score.
* **Release**: headline, recommendation, criterion-by-criterion evidence, risks.

## 6. Human gate: approval (30 s)
* Approve with a comment. The change is merged into `main` of the target repository and a deployment record is
  written. Show the Audit tab: "chain intact". Show Metrics: tokens, time and estimated cost per agent.

## 7. The same thing in the terminal (1 min)

```powershell
.\.venv\Scripts\forgeline.exe chat --provider gemini
```

Paste a small requirement (finish with a line containing only `.`), watch each agent stream, answer the gates.
Then `forgeline status`, `forgeline metrics <run>`, `forgeline audit <run>`.

## 8. Failure handling (1-2 min)
* **Security block**: type a requirement that asks to "log refund details including the card used". If the
  Developer logs the full card number, the security rule blocks it, the Developer gets one fix attempt, otherwise
  an escalation asks a human (an override needs a written justification).
* **Provider outage**: our live run hit a real Gemini "503 high demand" and the timeline shows the automatic
  failover to Ollama. To force it: set `GEMINI_MODEL_CODE=gemini-does-not-exist` in `.env` and start a run.
  With both providers down the run pauses; `forgeline resume <run> -p ollama` continues from the checkpoint.
* **Every failure scenario, deterministically**:
  `.\.venv\Scripts\python.exe -m pytest -n 6 tests/test_workflow.py` (scope violation, repeated action,
  design disagreement and deadlock, retry budget, malformed output, security block + override, provider outage +
  resume, request changes).

## 9. Close (20 s)
Limitations: simulated deployment, process-level sandbox, one Python repository, free-tier quotas
(see README).
