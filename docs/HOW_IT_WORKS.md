# Forgeline explained simply

## 1. The problem in one paragraph

A software team receives a request ("let merchants refund part of a payment"). Someone has to clarify it,
work out which code is affected, design the change, write the code, write tests, check for security problems,
and finally decide whether it is safe to ship. Forgeline hands each of those jobs to a specialised AI **agent**, the
way a company hands them to different people, and keeps a **human** in charge of the decisions that matter:
answering open questions, approving the merge and deployment, and overriding anything risky.

## 2. What we built

| Piece | What it is | Where |
|---|---|---|
| Seven agents | Analyst, Architect, QA, Developer, Security, Evaluator, Release, each with one job and a strict output format | `forgeline/agents/` |
| The workflow | A LangGraph "flowchart" that passes work from agent to agent, loops back when something fails, and pauses for humans | `forgeline/graph/` |
| Safety guards | Scope guard, sandbox, protected files, retry limits, security blocking, action ledger | `forgeline/tools/`, `policy.yaml` |
| Model layer | Talks to Google Gemini (cloud) or Ollama (on this laptop); switches automatically if one fails; streams text live | `forgeline/llm/` |
| Memory | SQLite database: every run, event, model call, approval and a tamper-proof audit log; checkpoints so runs can resume | `forgeline/store/`, `data/` |
| Agent console | Web app: type a requirement, watch every agent write its answer live, approve or reject | `dashboard/` + `forgeline/api/` |
| Agent CLI | `forgeline chat`: the same live experience in the terminal | `forgeline/chat.py`, `forgeline/cli.py` |
| Sample repository | PayLite, a small payments service the agents actually change | `sample_repo/` |
| Hidden baseline | Tests and a reference solution written by a human, never shown to the agents, used to grade them | `baseline/` |

## 3. What happens when you type a requirement

1. **Workspace.** Forgeline makes a private copy of the repository on its own git branch (`agent/<run id>`). Agents
   can never touch the main branch.
2. **Analyst.** Reads the requirement and the code map and writes *acceptance criteria* ("Given a captured
   payment of 100, when 40 is refunded, then 60 remains refundable") and a task list. If something important is
   unclear, it **asks you** and waits.
3. **Architect.** Reads the code structure and decides exactly which files must change. That list becomes the
   **only** files the Developer is allowed to touch.
4. **QA (tests first).** Writes automated tests from the acceptance criteria *before* any code exists. Forgeline runs
   them on the old code: they must fail, and fail for the right reason (the feature is missing, not a typo in the
   test). This gives an independent check of the Developer's work.
5. **Developer.** Writes the change as small "find this, replace with that" edits.
6. **Scope guard.** Before anything is written to disk, checks every file: is it on the Architect's list? Is it
   protected (tests used for grading, config, git internals)? Does it escape the folder? If not allowed, the change
   is rejected and the Developer is told why.
7. **Tests and lint.** Runs all tests (old ones plus QA's new ones) and a code-style check. If anything fails,
   the exact failure messages go back to the Developer, who tries again (at most 3 times).
8. **Security.** Scans the changed lines for problems such as logging a customer's full card number or a
   hard-coded password. A critical finding **blocks the release**; the Developer gets one chance to fix it.
9. **Evaluator.** Secretly runs the human-written tests and scores the work on a rubric. The agents never see
   these tests, so this is an honest grade.
10. **Release.** Collects the evidence (tests, coverage, security result, score, risks) into a summary with a
    recommendation, and commits the change to the agent branch.
11. **Your approval.** You read the evidence and choose: **approve** (merge into main + record a deployment),
    **request changes** (back to the Developer with your comment) or **reject**.

While this runs, every agent's text appears **live, as it is being written**, and is then shown as a neat
result (criteria table, design, test code, code edits, test results, score).

## 4. What happens when things go wrong

| Situation | What Forgeline does |
|---|---|
| The code fails tests | Sends the failing test messages to the Developer; up to 3 attempts; then asks a human |
| The Developer thinks the design is wrong | The Developer raises an objection; the Architect revises once; if they still disagree, a human decides |
| A critical security problem | Release is blocked; one automatic fix attempt; then a human must fix, retry or override (override requires a written reason) |
| A model returns garbage | Tells the model exactly what was wrong and asks again; tries a slightly different setting; then tries the other model provider; then asks a human |
| The model provider goes down (we saw real Gemini "503 high demand" errors) | Retries, then switches to the other provider automatically. If both are down the run pauses safely and continues later from where it stopped |
| An agent repeats an action or edits an unrelated file | Repeated actions are detected and skipped; edits outside the approved files are refused before reaching disk |

All the limits (how many retries, which files are protected, which decisions need a human) are in one readable
file: `policy.yaml`.

## 5. Where the agents must stop

Agents may retry, fix and revise **within their limits**. Only a human can: answer open product questions,
merge, deploy, accept a security risk, break a design deadlock, or allow more attempts. The merge function is not
available to any agent at all.

## 6. How we can trust what happened

* **Live event log**: every step, model call, retry, failover and decision, visible in the console.
* **Usage and cost**: tokens, time and estimated cost per agent and provider.
* **Tamper-proof audit log**: each record contains a fingerprint (SHA-256 hash) of the previous one, so editing or
  deleting any record is detected (`forgeline audit <run>` shows "chain intact").
* **Checkpoints**: the full state is saved after every step.

## 7. How it is built (technical summary)

* **Python 3.12, LangGraph** for the workflow: 16 nodes, conditional routing, `interrupt()` for human gates, a
  SQLite checkpointer for pause and resume.
* **Pydantic** contracts for every agent's output; a repair loop validates them.
* **Two output styles**: short answers are JSON; code is plain text (fenced code and SEARCH/REPLACE blocks),
  because small local models break JSON when code contains quotes.
* **Tools are ordinary programs**, not AI: git worktrees, an AST code index, pytest, ruff, bandit, plus our own
  payment-security rules. The AI decides; the tools verify.
* **FastAPI** server with Server-Sent Events for live updates; **React + Tailwind** console.
* **132 automated tests** drive the real workflow through every failure scenario using a scripted model, so
  they need no internet and cost nothing.

## 8. Honest limitations

* Deployment is simulated (a record in `workspace/deployments.jsonl`), and "merge" is a local git merge.
* The sandbox limits which programs run, where they run and which secrets they see, but it is not a container.
* The agents work on one Python repository (PayLite).
* Gemini's free tier has small daily limits; the local model is slow on a 4 GB graphics card (it is the
  backup path).
* AI output varies run to run; the automated tests are what guarantee each behaviour.
