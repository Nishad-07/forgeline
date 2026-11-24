// Fixture data for REQ-101 "Partial refunds": final run state, event script, diff, usage.

import type {
  ClarificationGate,
  DiffResponse,
  LLMCall,
  ReleaseApprovalGate,
  RunState,
  UsageByAgent,
} from "../types";

export interface ScriptStep {
  at: number; // seconds after run start
  node: string | null;
  kind: string;
  payload?: Record<string, unknown>;
  /** ms to wait before this step (mock replay pacing override) */
  gap?: number;
}

export const REQ101_BRANCH = "forgeline/req-101-7f3a9c21";
export const REQ101_COMMIT = "9c41e7ab52d0f13e86a4b7c2d9e05f1a3b68c4d7";

export const req101State: RunState = {
  analysis: {
    summary:
      "Allow merchants to refund part of a captured payment, one or more times, without ever refunding more than was captured. Payments gain refunded/refundable totals and a PARTIALLY_REFUNDED status, while omitting the amount keeps today's full-refund behaviour.",
    acceptance_criteria: [
      {
        id: "AC-1",
        given: "a captured payment of ₹100.00 (10000 paise)",
        when: "the merchant refunds 4000 paise",
        then: "a refund of 4000 paise is created and the payment status becomes PARTIALLY_REFUNDED with refundable_paise 6000",
      },
      {
        id: "AC-2",
        given: "a captured payment with 6000 paise still refundable",
        when: "the merchant refunds 6000 paise",
        then: "the payment status becomes REFUNDED and refundable_paise is 0",
      },
      {
        id: "AC-3",
        given: "a captured payment with 6000 paise still refundable",
        when: "the merchant requests a refund of 7000 paise",
        then: "the API responds 400 REFUND_EXCEEDS_REMAINING and no refund is recorded",
      },
      {
        id: "AC-4",
        given: "any captured payment",
        when: "the merchant sends amount_paise of 0, a negative number or a non-integer",
        then: "the API responds 400 INVALID_AMOUNT",
      },
      {
        id: "AC-5",
        given: "a partially refunded payment",
        when: "the merchant omits amount_paise",
        then: "the entire remaining refundable amount is refunded (backwards compatible)",
      },
      {
        id: "AC-6",
        given: "a payment that is not captured or already fully refunded",
        when: "the merchant requests any refund",
        then: "the API responds 409 PAYMENT_NOT_REFUNDABLE and GET /payments/{id} still lists the full refund history",
      },
    ],
    tasks: [
      {
        id: "T-1",
        title: "Extend payment model",
        description: "Add PARTIALLY_REFUNDED status and refunded/refundable helpers computed from stored refunds.",
      },
      {
        id: "T-2",
        title: "Partial refund logic",
        description: "Accept optional amount_paise in refund_payment, validate it and enforce the remaining refundable limit.",
      },
      {
        id: "T-3",
        title: "API contract",
        description: "Accept amount_paise in POST /payments/{id}/refunds and expose refunded_paise / refundable_paise on GET.",
      },
      {
        id: "T-4",
        title: "Notifications",
        description: "Notify the merchant on every refund, including the remaining refundable amount in the payload.",
      },
    ],
    clarifying_questions: [],
    assumptions: [
      "Amounts are integers in paise; no fractional currency handling is required.",
      "Refund history is derived from stored Refund records; no separate ledger is introduced.",
      "Concurrent refunds on the same payment are serialised by the in-memory store (single process).",
    ],
    out_of_scope: ["Multi-currency refunds", "Refunds to a different card", "Asynchronous settlement with the card network"],
    ambiguity_score: 0.22,
  },
  clarifications: [
    {
      question: "Should merchants be notified for every partial refund, or only once the payment is fully refunded?",
      answer: "Notify on every partial refund and include the remaining refundable amount in the payload.",
    },
    {
      question: "Should a refund reason become mandatory for partial refunds?",
      answer: "No. Keep reason optional to stay backwards compatible with existing clients.",
    },
  ],
  design: {
    summary:
      "Derive refunded totals from the stored refunds instead of adding mutable counters. refund_payment gains an optional amount_paise, validated against the remaining refundable amount; the API layer passes it through and serialises the new fields.",
    impacted_components: [
      {
        path: "app/models.py",
        change_type: "modify",
        reason: "Add PaymentStatus.PARTIALLY_REFUNDED; payments in that status remain refundable.",
      },
      {
        path: "app/refunds.py",
        change_type: "modify",
        reason: "Optional amount_paise, INVALID_AMOUNT / REFUND_EXCEEDS_REMAINING validation, status transitions, refunded/refundable helpers.",
      },
      {
        path: "app/main.py",
        change_type: "modify",
        reason: "RefundIn.amount_paise, pass it to refund_payment, expose refunded_paise and refundable_paise in payment_out.",
      },
    ],
    context_files: ["app/errors.py", "app/payments.py", "app/store.py", "app/notifications.py", "tests/test_refunds.py"],
    untouched_components: ["app/merchants.py", "app/payments.py (capture flow)", "app/store.py", "tests/golden/**"],
    design_notes:
      "Totals are computed from the refund list so history and totals can never disagree. Validation happens before any mutation, so a rejected refund changes nothing. A new ValidationError code (400) is reused from app/errors.py.",
    test_strategy:
      "Acceptance tests (written first by the QA agent) cover each criterion through the HTTP API and the Python function: single and multiple partial refunds, exact remaining amount, over-refund, invalid amounts, omitted amount, and non-capturable states.",
    risks: [
      "Existing clients rely on status REFUNDED after a refund without amount: covered by AC-5.",
      "Notification volume increases because every partial refund notifies the merchant.",
    ],
  },
  impact_warnings: [
    "app/payments.py imports PaymentStatus: new enum member must not break get_payment() checks.",
    "tests/test_refunds.py asserts status == REFUNDED after a full refund; behaviour preserved.",
  ],
  acceptance: {
    path: "tests/test_acceptance_req_101.py",
    covered_criteria: ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6"],
    baseline: { passed: 5, failed: 9, errors: 0 },
    warning: "",
    content: "",
  },
  dev_attempt: 3,
  dev_budget: 3,
  feedback: [
    "TEST FAILED tests/test_acceptance_req_101.py::test_refund_exceeding_remaining_is_rejected: AssertionError: assert 201 == 400 — the over-refund was accepted; validate amount_paise against refundable_paise before creating the Refund.",
    "TEST FAILED tests/test_acceptance_req_101.py::test_get_payment_exposes_refund_totals: KeyError: 'refundable_paise' — payment_out() does not include the new fields.",
  ],
  scope: { ok: true, files: ["app/refunds.py", "app/main.py"], violations: [] },
  qa: {
    ok: true,
    dev_files: ["app/models.py", "app/refunds.py", "app/main.py"],
    tests: {
      passed: 41,
      failed: 0,
      errors: 0,
      skipped: 0,
      total: 41,
      ok: true,
      coverage_percent: 98.7,
      coverage_by_file: {
        "app/main.py": 97.6,
        "app/models.py": 100,
        "app/refunds.py": 100,
        "app/payments.py": 98.2,
        "app/errors.py": 100,
        "app/notifications.py": 94.1,
      },
      cases: [
        ["tests/test_acceptance_req_101.py::test_partial_refund_sets_partially_refunded", 0.021],
        ["tests/test_acceptance_req_101.py::test_second_partial_refund_completes_payment", 0.018],
        ["tests/test_acceptance_req_101.py::test_refund_exceeding_remaining_is_rejected", 0.016],
        ["tests/test_acceptance_req_101.py::test_refund_exceeding_remaining_changes_nothing", 0.017],
        ["tests/test_acceptance_req_101.py::test_zero_amount_is_invalid", 0.011],
        ["tests/test_acceptance_req_101.py::test_negative_amount_is_invalid", 0.010],
        ["tests/test_acceptance_req_101.py::test_omitted_amount_refunds_remaining", 0.019],
        ["tests/test_acceptance_req_101.py::test_get_payment_exposes_refund_totals", 0.014],
        ["tests/test_acceptance_req_101.py::test_fully_refunded_payment_rejects_refund", 0.015],
        ["tests/test_acceptance_req_101.py::test_uncaptured_payment_rejects_refund", 0.012],
        ["tests/test_acceptance_req_101.py::test_refund_history_is_kept", 0.016],
        ["tests/test_acceptance_req_101.py::test_python_api_mirrors_http", 0.009],
        ["tests/test_acceptance_req_101.py::test_notification_sent_per_partial_refund", 0.013],
        ["tests/test_acceptance_req_101.py::test_amount_must_be_integer", 0.010],
        ["tests/test_refunds.py::test_full_refund_of_captured_payment", 0.012],
        ["tests/test_refunds.py::test_refund_of_uncaptured_payment_fails", 0.009],
        ["tests/test_refunds.py::test_list_refunds_sorted", 0.011],
        ["tests/test_refunds.py::test_refund_notifies_merchant", 0.010],
        ["tests/test_payments.py::test_create_payment", 0.008],
        ["tests/test_payments.py::test_capture_payment", 0.009],
        ["tests/test_payments.py::test_capture_twice_fails", 0.008],
        ["tests/test_payments.py::test_masked_card", 0.004],
        ["tests/test_api.py::test_post_refund_full", 0.024],
        ["tests/test_api.py::test_get_payment", 0.017],
        ["tests/test_api.py::test_list_refunds_endpoint", 0.021],
        ["tests/test_merchants.py::test_create_merchant", 0.006],
      ].map(([nodeid, d]) => ({ nodeid: nodeid as string, outcome: "passed", duration_s: d as number, message: "" })),
    },
    lint: { ok: true, issues: [] },
  },
  security: {
    findings: [
      {
        rule: "B101:assert_used",
        severity: "low",
        file: "app/store.py",
        line: 27,
        message: "Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.",
        evidence: "assert key not in self.refunds",
        source: "bandit",
        introduced: false,
      },
      {
        rule: "card-number-exposure",
        severity: "medium",
        file: "app/models.py",
        line: 34,
        message: "Raw card number stored on the Payment model (pre-existing tech debt).",
        evidence: "card_number: str",
        source: "custom",
        introduced: false,
      },
      {
        rule: "broad-exception",
        severity: "low",
        file: "app/refunds.py",
        line: 58,
        message: "Notification failure swallowed with a broad except; consider logging the exception type.",
        evidence: "except Exception:  # notifications are best effort",
        source: "custom",
        introduced: true,
      },
    ],
    blocking: [],
    introduced_count: 1,
    triage: {
      summary:
        "No blocking issues. The only introduced finding is a best-effort notification guard that is acceptable; pre-existing card storage is tracked as tech debt and is not touched by this change.",
      assessments: [
        {
          rule: "broad-exception",
          file: "app/refunds.py",
          line: 58,
          verdict: "acceptable",
          explanation: "Notifications are explicitly best effort; the refund is already committed and the failure is logged.",
        },
        {
          rule: "card-number-exposure",
          file: "app/models.py",
          line: 34,
          verdict: "pre-existing",
          explanation: "Not introduced by this change; the diff never reads or logs card_number.",
        },
        {
          rule: "B101:assert_used",
          file: "app/store.py",
          line: 27,
          verdict: "false_positive",
          explanation: "Internal invariant in the in-memory store, not a security control.",
        },
      ],
      additional_concerns: [],
    },
  },
  security_fix_attempts: 0,
  attempts_log: [
    {
      attempt: 1,
      ts: "",
      stage: "scope",
      scope_ok: false,
      violations: [
        {
          path: "pyproject.toml",
          rule: "protected_path",
          reason: "pyproject.toml is protected by policy (scope.protected_paths)",
        },
      ],
    },
    { attempt: 2, ts: "", stage: "qa", scope_ok: true, passed: 39, failed: 2, errors: 0, coverage: 96.1, lint_issues: 0 },
    { attempt: 3, ts: "", stage: "qa", scope_ok: true, passed: 41, failed: 0, errors: 0, coverage: 98.7, lint_issues: 0 },
  ],
  evaluation: {
    golden: { available: true, passed: 12, total: 12, failures: [] },
    rubric: {
      items: [
        { criterion: "Requirement coverage", score: 5, justification: "All six acceptance criteria are implemented and evidenced by passing tests." },
        { criterion: "Correctness and edge cases", score: 5, justification: "Over-refund and invalid amounts are rejected before any mutation; exact remaining amount is handled." },
        { criterion: "Scope discipline", score: 5, justification: "Only the three architect-approved files changed; no protected paths touched after the guard rejection." },
        { criterion: "Code quality", score: 4, justification: "Readable and idiomatic; the refunded total is recomputed on every call, which is fine at this scale." },
        { criterion: "Backward compatibility", score: 5, justification: "Omitting amount_paise keeps the previous full-refund behaviour and response shape." },
      ],
      overall_comment:
        "A clean, minimal implementation that matches the human reference closely. Behaviour is fully covered by the hidden golden suite.",
    },
    scores: { golden_pass_rate: 1, rubric_percent: 96, overall: 98.4 },
    reference_files: ["app/models.py", "app/refunds.py", "app/main.py"],
    agent_files: ["app/models.py", "app/refunds.py", "app/main.py"],
  },
  release: {
    headline: "Partial refunds for captured payments",
    change_summary:
      "Merchants can now refund part of a captured payment, several times, up to the captured amount. Payments expose refunded_paise and refundable_paise and move through PARTIALLY_REFUNDED to REFUNDED.",
    recommendation: "approve",
    recommendation_reason:
      "All 41 tests and 12/12 hidden golden tests pass, coverage is 98.7%, there are no blocking security findings and the change stayed within the approved scope.",
    criteria_evidence: [
      { criterion_id: "AC-1", status: "met", evidence: "test_partial_refund_sets_partially_refunded" },
      { criterion_id: "AC-2", status: "met", evidence: "test_second_partial_refund_completes_payment" },
      { criterion_id: "AC-3", status: "met", evidence: "test_refund_exceeding_remaining_is_rejected, …_changes_nothing" },
      { criterion_id: "AC-4", status: "met", evidence: "test_zero_amount_is_invalid, test_negative_amount_is_invalid" },
      { criterion_id: "AC-5", status: "met", evidence: "test_omitted_amount_refunds_remaining" },
      { criterion_id: "AC-6", status: "met", evidence: "test_fully_refunded_payment_rejects_refund, test_refund_history_is_kept" },
    ],
    known_risks: [
      "Merchants receive one notification per partial refund (agreed during clarification).",
      "Totals are recomputed from refund history; fine for the in-memory store, revisit for a database with many refunds.",
    ],
    rollback_plan: "Revert the merge commit; no data migration is involved.",
    facts: {},
    diff_stat: { files: 3, added: 58, removed: 9 },
    branch: REQ101_BRANCH,
    commit: REQ101_COMMIT,
  },
  human_decisions: [],
};

export const req101Facts: Record<string, unknown> = {
  all_tests_passed: true,
  tests: "41 passed, 0 failed, 0 errors",
  coverage_percent: 98.7,
  lint_issues: 0,
  blocking_security_findings: 0,
  security_override: false,
  scope_verdict: "within the architect-approved files",
  developer_attempts: 3,
  golden_tests_hidden_from_agents: 12,
};
req101State.release!.facts = req101Facts;

export const req101ClarificationGate: ClarificationGate = {
  gate: "clarification",
  questions: [
    "Should merchants be notified for every partial refund, or only once the payment is fully refunded?",
    "Should a refund reason become mandatory for partial refunds?",
  ],
  requirement: "REQ-101 Partial refunds",
  assumptions: [
    "Amounts are integers in paise; no fractional currency handling is required.",
    "Refund history is derived from stored Refund records.",
  ],
};

export const req101ApprovalGate: ReleaseApprovalGate = {
  gate: "release_approval",
  headline: req101State.release!.headline,
  recommendation: "approve",
  reason: req101State.release!.recommendation_reason,
  branch: REQ101_BRANCH,
  commit: REQ101_COMMIT,
  facts: req101Facts,
  scores: req101State.evaluation!.scores,
  known_risks: req101State.release!.known_risks,
};

/** Event script up to the release approval gate. `clarify` pauses after `gate_opened` (index marked). */
export const req101Script: ScriptStep[] = [
  { at: 0, node: null, kind: "run_created", payload: { requirement: "REQ-101", provider: "gemini" } },
  { at: 1, node: "intake", kind: "node_start" },
  { at: 3, node: "intake", kind: "node_end", payload: { duration_ms: 1840, summary: `worktree ${REQ101_BRANCH} @ 4e1b9d02` } },
  { at: 3, node: "analyst", kind: "node_start" },
  { at: 11, node: "analyst", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 7420, input_tokens: 5210, output_tokens: 1380, repairs: 0 } },
  { at: 12, node: "analyst", kind: "node_end", payload: { duration_ms: 8650, summary: "6 criteria, 4 tasks, 2 clarifying question(s)" } },
  { at: 12, node: "clarify", kind: "node_start" },
  { at: 12, node: null, kind: "gate_opened", payload: { gate: req101ClarificationGate } },
  // --- human answers the clarification gate
  { at: 95, node: null, kind: "gate_answered", payload: { gate: "clarification", decision: "answered", actor: "Priya Sharma" } },
  { at: 96, node: "clarify", kind: "node_start" },
  { at: 96, node: "clarify", kind: "node_end", payload: { duration_ms: 38, summary: "2 answer(s) from Priya Sharma" } },
  { at: 96, node: "analyst", kind: "node_start" },
  { at: 104, node: "analyst", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 7010, input_tokens: 5980, output_tokens: 1420, repairs: 0 } },
  { at: 105, node: "analyst", kind: "node_end", payload: { duration_ms: 8320, summary: "6 criteria, 4 tasks, 0 clarifying question(s)" } },
  { at: 105, node: "architect", kind: "node_start" },
  { at: 108, node: "architect", kind: "llm_retry", payload: { provider: "gemini", delay_s: 2, error: "503 UNAVAILABLE: the model is overloaded" } },
  { at: 112, node: "architect", kind: "llm_failover", payload: { from: "gemini", to: "ollama", error: "503 UNAVAILABLE: The model is overloaded. Please try again later." } },
  { at: 131, node: "architect", kind: "llm_call", payload: { provider: "ollama", model: "qwen2.5-coder:14b", latency_ms: 18900, input_tokens: 7340, output_tokens: 1610, repairs: 0 } },
  { at: 132, node: "architect", kind: "node_end", payload: { duration_ms: 26840, summary: "3 file(s): app/main.py, app/models.py, app/refunds.py" } },
  { at: 132, node: "qa_author", kind: "node_start" },
  { at: 146, node: "qa", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 12800, input_tokens: 8120, output_tokens: 3240, repairs: 0 } },
  { at: 151, node: "qa_author", kind: "node_end", payload: { duration_ms: 18950, summary: "14 tests; 9 fail before the change (expected)" } },
  { at: 151, node: "developer", kind: "node_start" },
  { at: 166, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 14200, input_tokens: 11850, output_tokens: 2960, repairs: 0 } },
  { at: 167, node: "developer", kind: "agent_output_rejected", payload: { attempt: 1, problems: ["app/refunds.py: edit anchor matched 0 times", "change touches pyproject.toml which is not in the design"] } },
  { at: 181, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 13600, input_tokens: 12410, output_tokens: 3020, repairs: 1 } },
  { at: 182, node: "developer", kind: "node_end", payload: { duration_ms: 30910, summary: "attempt 1: 4 file(s), 9 edit(s)" } },
  { at: 182, node: "apply_change", kind: "node_start" },
  { at: 182, node: "apply_change", kind: "scope_violation", payload: { violations: [{ path: "pyproject.toml", rule: "protected_path", reason: "pyproject.toml is protected by policy (scope.protected_paths)" }] } },
  { at: 183, node: "apply_change", kind: "node_end", payload: { duration_ms: 112, summary: "change rejected before writing" } },
  { at: 183, node: "developer", kind: "node_start" },
  { at: 197, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 13900, input_tokens: 12980, output_tokens: 2710, repairs: 0 } },
  { at: 198, node: "developer", kind: "node_end", payload: { duration_ms: 14420, summary: "attempt 2: 3 file(s), 7 edit(s)" } },
  { at: 198, node: "apply_change", kind: "node_start" },
  { at: 198, node: "apply_change", kind: "node_end", payload: { duration_ms: 96, summary: "wrote 3 file(s) within approved scope" } },
  { at: 198, node: "qa_run", kind: "node_start" },
  { at: 210, node: "qa_run", kind: "node_end", payload: { duration_ms: 11840, summary: "39 passed, 2 failed, 0 errors; coverage 96.1%; 0 lint issue(s)" } },
  { at: 210, node: "developer", kind: "node_start" },
  { at: 223, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 12700, input_tokens: 13650, output_tokens: 1180, repairs: 0 } },
  { at: 224, node: "developer", kind: "node_end", payload: { duration_ms: 13310, summary: "attempt 3: 2 file(s), 2 edit(s)" } },
  { at: 224, node: "apply_change", kind: "node_start" },
  { at: 224, node: "apply_change", kind: "node_end", payload: { duration_ms: 88, summary: "wrote 2 file(s) within approved scope" } },
  { at: 224, node: "qa_run", kind: "node_start" },
  { at: 236, node: "qa_run", kind: "node_end", payload: { duration_ms: 12020, summary: "41 passed, 0 failed, 0 errors; coverage 98.7%; 0 lint issue(s)" } },
  { at: 236, node: "security", kind: "node_start" },
  { at: 247, node: "security", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 6900, input_tokens: 6420, output_tokens: 980, repairs: 0 } },
  { at: 248, node: "security", kind: "node_end", payload: { duration_ms: 12110, summary: "3 finding(s), 0 blocking" } },
  { at: 248, node: "evaluate", kind: "node_start" },
  { at: 266, node: "evaluator", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 8100, input_tokens: 9870, output_tokens: 1240, repairs: 0 } },
  { at: 267, node: "evaluate", kind: "node_end", payload: { duration_ms: 18760, summary: "golden 12/12; overall 98.4" } },
  { at: 267, node: "release", kind: "node_start" },
  { at: 279, node: "release", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 9400, input_tokens: 8640, output_tokens: 1530, repairs: 0 } },
  { at: 281, node: "release", kind: "node_end", payload: { duration_ms: 13880, summary: `recommendation: approve; commit ${REQ101_COMMIT.slice(0, 8)}` } },
  { at: 281, node: "approval", kind: "node_start" },
  { at: 281, node: null, kind: "gate_opened", payload: { gate: req101ApprovalGate } },
];

/** Index of the first step after the clarification gate (where a replay resumes). */
export const REQ101_CLARIFY_RESUME_INDEX = req101Script.findIndex((s) => s.kind === "gate_answered");

export const req101Diff: DiffResponse = {
  stat: { files: 3, added: 58, removed: 9 },
  diff: `diff --git a/app/main.py b/app/main.py
index 3b1f0a2..8d7c4e1 100644
--- a/app/main.py
+++ b/app/main.py
@@ -34,6 +34,7 @@ class PaymentIn(BaseModel):

 class RefundIn(BaseModel):
     reason: str = ""
+    amount_paise: int | None = None


 def payment_out(payment: Payment) -> dict[str, Any]:
@@ -42,6 +43,8 @@ def payment_out(payment: Payment) -> dict[str, Any]:
         "currency": payment.currency,
         "card": payment.masked_card,
         "status": payment.status.value,
+        "refunded_paise": refunded_total(payment.id),
+        "refundable_paise": refundable_amount(payment),
     }


@@ -87,7 +90,9 @@ def capture(payment_id: str) -> dict[str, Any]:

 @app.post("/payments/{payment_id}/refunds", status_code=201)
 def post_refund(payment_id: str, body: RefundIn) -> dict[str, Any]:
-    return refund_out(refund_payment(payment_id, reason=body.reason))
+    return refund_out(
+        refund_payment(payment_id, amount_paise=body.amount_paise, reason=body.reason)
+    )


 @app.get("/payments/{payment_id}/refunds")
diff --git a/app/models.py b/app/models.py
index 9a7e2c4..f01b6d3 100644
--- a/app/models.py
+++ b/app/models.py
@@ -14,6 +14,7 @@ def utcnow() -> datetime:
 class PaymentStatus(StrEnum):
     CREATED = "CREATED"
     CAPTURED = "CAPTURED"
+    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
     REFUNDED = "REFUNDED"


diff --git a/app/refunds.py b/app/refunds.py
index 51c9d0e..b7a24f8 100644
--- a/app/refunds.py
+++ b/app/refunds.py
@@ -1,39 +1,84 @@
-"""Refunds. Currently only full refunds of captured payments are supported."""
+"""Refunds: full and partial refunds of captured payments."""

 from __future__ import annotations

 import logging

-from app.errors import InvalidStateError
-from app.models import PaymentStatus, Refund
+from app.errors import InvalidStateError, ValidationError
+from app.models import Payment, PaymentStatus, Refund
 from app.notifications import notify
 from app.payments import get_payment
 from app.store import new_id, store

 logger = logging.getLogger("paylite.refunds")

+REFUNDABLE_STATUSES = {PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED}

-def refund_payment(payment_id: str, reason: str = "") -> Refund:
-    """Refund the full captured amount of a payment."""
+
+def refunded_total(payment_id: str) -> int:
+    """Total amount refunded so far for a payment, in paise."""
+    return sum(r.amount_paise for r in store.refunds.values() if r.payment_id == payment_id)
+
+
+def refundable_amount(payment: Payment) -> int:
+    """Amount still refundable for a payment, in paise."""
+    if payment.status not in REFUNDABLE_STATUSES:
+        return 0
+    return payment.amount_paise - refunded_total(payment.id)
+
+
+def refund_payment(payment_id: str, amount_paise: int | None = None, reason: str = "") -> Refund:
+    """Refund part or all of the remaining refundable amount of a captured payment."""
     payment = get_payment(payment_id)
-    if payment.status != PaymentStatus.CAPTURED:
+    if payment.status not in REFUNDABLE_STATUSES:
         raise InvalidStateError(
             f"Payment in status {payment.status.value} cannot be refunded", code="PAYMENT_NOT_REFUNDABLE"
         )

+    remaining = refundable_amount(payment)
+    if amount_paise is None:
+        amount_paise = remaining
+    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int) or amount_paise <= 0:
+        raise ValidationError("amount_paise must be a positive integer", code="INVALID_AMOUNT")
+    if amount_paise > remaining:
+        raise ValidationError(
+            f"Refund of {amount_paise} exceeds the remaining {remaining} paise",
+            code="REFUND_EXCEEDS_REMAINING",
+        )
+
     refund = Refund(
-        id=new_id("rfnd"), payment_id=payment.id, amount_paise=payment.amount_paise, reason=reason
+        id=new_id("rfnd"), payment_id=payment.id, amount_paise=amount_paise, reason=reason
     )
     store.refunds[refund.id] = refund
-    payment.status = PaymentStatus.REFUNDED
+    remaining -= amount_paise
+    payment.status = PaymentStatus.REFUNDED if remaining == 0 else PaymentStatus.PARTIALLY_REFUNDED
     logger.info("refund issued id=%s payment=%s amount=%s", refund.id, payment.id, refund.amount_paise)
-    notify(
-        payment.merchant_id,
-        "refund.created",
-        {"refund_id": refund.id, "payment_id": payment.id, "amount_paise": refund.amount_paise},
-    )
+    try:
+        notify(
+            payment.merchant_id,
+            "refund.created",
+            {
+                "refund_id": refund.id,
+                "payment_id": payment.id,
+                "amount_paise": refund.amount_paise,
+                "refundable_paise": remaining,
+            },
+        )
+    except Exception:  # notifications are best effort
+        logger.warning("refund notification failed for payment=%s", payment.id)
     return refund


 def list_refunds(payment_id: str) -> list[Refund]:
`,
};

export const req101UsageByAgent: UsageByAgent[] = [
  { agent: "analyst", provider: "gemini", model: "gemini-2.5-flash", calls: 2, input_tokens: 11190, output_tokens: 2800, latency_ms: 14430, cost_usd: 0.0104, cache_hits: 0, failures: 0 },
  { agent: "architect", provider: "gemini", model: "gemini-2.5-pro", calls: 2, input_tokens: 0, output_tokens: 0, latency_ms: 6200, cost_usd: 0, cache_hits: 0, failures: 2 },
  { agent: "architect", provider: "ollama", model: "qwen2.5-coder:14b", calls: 1, input_tokens: 7340, output_tokens: 1610, latency_ms: 18900, cost_usd: 0, cache_hits: 0, failures: 0 },
  { agent: "qa", provider: "gemini", model: "gemini-2.5-pro", calls: 1, input_tokens: 8120, output_tokens: 3240, latency_ms: 12800, cost_usd: 0.0426, cache_hits: 0, failures: 0 },
  { agent: "developer", provider: "gemini", model: "gemini-2.5-pro", calls: 4, input_tokens: 50890, output_tokens: 9870, latency_ms: 54400, cost_usd: 0.1623, cache_hits: 0, failures: 1 },
  { agent: "security", provider: "gemini", model: "gemini-2.5-flash", calls: 1, input_tokens: 6420, output_tokens: 980, latency_ms: 6900, cost_usd: 0.0044, cache_hits: 0, failures: 0 },
  { agent: "evaluator", provider: "gemini", model: "gemini-2.5-flash", calls: 1, input_tokens: 9870, output_tokens: 1240, latency_ms: 8100, cost_usd: 0.0061, cache_hits: 0, failures: 0 },
  { agent: "release", provider: "gemini", model: "gemini-2.5-flash", calls: 1, input_tokens: 8640, output_tokens: 1530, latency_ms: 9400, cost_usd: 0.0064, cache_hits: 0, failures: 0 },
];

type CallSeed = Omit<LLMCall, "id" | "ts" | "cached"> & { at: number };

export const req101CallSeeds: CallSeed[] = [
  { at: 11, agent: "analyst", provider: "gemini", model: "gemini-2.5-flash", tier: "light", input_tokens: 5210, output_tokens: 1380, latency_ms: 7420, cost_usd: 0.0051, attempt: 1, status: "ok", error: null },
  { at: 104, agent: "analyst", provider: "gemini", model: "gemini-2.5-flash", tier: "light", input_tokens: 5980, output_tokens: 1420, latency_ms: 7010, cost_usd: 0.0053, attempt: 1, status: "ok", error: null },
  { at: 108, agent: "architect", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 0, output_tokens: 0, latency_ms: 3100, cost_usd: 0, attempt: 1, status: "provider_error", error: "503 UNAVAILABLE: the model is overloaded" },
  { at: 112, agent: "architect", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 0, output_tokens: 0, latency_ms: 3100, cost_usd: 0, attempt: 2, status: "provider_error", error: "503 UNAVAILABLE: The model is overloaded. Please try again later." },
  { at: 131, agent: "architect", provider: "ollama", model: "qwen2.5-coder:14b", tier: "code", input_tokens: 7340, output_tokens: 1610, latency_ms: 18900, cost_usd: 0, attempt: 1, status: "ok", error: null },
  { at: 146, agent: "qa", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 8120, output_tokens: 3240, latency_ms: 12800, cost_usd: 0.0426, attempt: 1, status: "ok", error: null },
  { at: 166, agent: "developer", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 11850, output_tokens: 2960, latency_ms: 14200, cost_usd: 0.0444, attempt: 1, status: "invalid_output", error: "semantic check failed: edit anchor matched 0 times" },
  { at: 181, agent: "developer", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 12410, output_tokens: 3020, latency_ms: 13600, cost_usd: 0.0457, attempt: 2, status: "repaired", error: null },
  { at: 197, agent: "developer", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 12980, output_tokens: 2710, latency_ms: 13900, cost_usd: 0.0433, attempt: 1, status: "ok", error: null },
  { at: 223, agent: "developer", provider: "gemini", model: "gemini-2.5-pro", tier: "code", input_tokens: 13650, output_tokens: 1180, latency_ms: 12700, cost_usd: 0.0289, attempt: 1, status: "ok", error: null },
  { at: 247, agent: "security", provider: "gemini", model: "gemini-2.5-flash", tier: "light", input_tokens: 6420, output_tokens: 980, latency_ms: 6900, cost_usd: 0.0044, attempt: 1, status: "ok", error: null },
  { at: 266, agent: "evaluator", provider: "gemini", model: "gemini-2.5-flash", tier: "light", input_tokens: 9870, output_tokens: 1240, latency_ms: 8100, cost_usd: 0.0061, attempt: 1, status: "ok", error: null },
  { at: 279, agent: "release", provider: "gemini", model: "gemini-2.5-flash", tier: "light", input_tokens: 8640, output_tokens: 1530, latency_ms: 9400, cost_usd: 0.0064, attempt: 1, status: "ok", error: null },
];

/** Audit seeds: [at seconds, actor, action, details]. */
export const req101AuditSeeds: [number, string, string, Record<string, unknown>][] = [
  [0, "system", "run_started", { requirement: "REQ-101", provider: "gemini", policy_sha256: "b3f1c07a9d2e…" }],
  [3, "system", "worktree_created", { branch: REQ101_BRANCH, base: "4e1b9d02" }],
  [12, "agent:analyst", "analysis_produced", { criteria: 6, tasks: 4, questions: 2, ambiguity: 0.58 }],
  [95, "human:Priya Sharma", "clarification_answered", { answers: 2 }],
  [105, "agent:analyst", "analysis_produced", { criteria: 6, tasks: 4, questions: 0, ambiguity: 0.22 }],
  [112, "system:llm_router", "provider_failover", { agent: "architect", from: "gemini", to: "ollama" }],
  [132, "agent:architect", "design_approved", { files: ["app/main.py", "app/models.py", "app/refunds.py"] }],
  [151, "agent:qa", "acceptance_tests_written", { tests: 14, failing_before_change: 9 }],
  [182, "system:scope_guard", "change_rejected", { violations: [{ path: "pyproject.toml", rule: "protected_path" }] }],
  [198, "agent:developer", "files_written", { files: ["app/models.py", "app/refunds.py", "app/main.py"], attempt: 2 }],
  [210, "agent:qa", "checks_run", { attempt: 2, passed: 39, failed: 2, coverage: 96.1, lint_issues: 0 }],
  [224, "agent:developer", "files_written", { files: ["app/refunds.py", "app/main.py"], attempt: 3 }],
  [236, "agent:qa", "checks_run", { attempt: 3, passed: 41, failed: 0, coverage: 98.7, lint_issues: 0 }],
  [248, "agent:security", "scan_completed", { findings: 3, blocking: 0, introduced: 1 }],
  [267, "agent:evaluator", "evaluation_completed", { golden: "12/12", overall: 98.4 }],
  [281, "agent:release", "branch_committed", { branch: REQ101_BRANCH, commit: REQ101_COMMIT }],
];
