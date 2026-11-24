// Fixture data for REQ-102 "Refund audit logging": a run escalated on a security block.

import type { DiffResponse, EscalationGate, Finding, RunState, UsageByAgent } from "../types";
import type { ScriptStep } from "./req101";

export const REQ102_BRANCH = "forgeline/req-102-b81e4d07";

const blockingFinding: Finding = {
  rule: "card-number-logged",
  severity: "high",
  file: "app/refunds.py",
  line: 41,
  message: "Full card number (PAN) written to the audit log. PCI DSS forbids storing or logging the full PAN.",
  evidence: 'audit.info("refund %s merchant=%s payment=%s amount=%s card=%s", refund.id, payment.merchant_id, payment.id, refund.amount_paise, payment.card_number)',
  source: "custom",
  introduced: true,
};

export const req102EscalationGate: EscalationGate = {
  gate: "escalation",
  reason: "security_block",
  node: "security",
  details:
    "1 blocking finding remains after 1 automatic fix attempt: [high] card-number-logged at app/refunds.py:41. The requirement asks support to see 'which card the payment was made with'; the developer kept logging the raw card number. A human must either guide a retry (e.g. log the masked card), override with a written justification, or abort.",
  resume_node: "developer",
  options: ["retry", "override", "abort"],
};

export const req102State: RunState = {
  analysis: {
    summary:
      "Write one structured audit log entry per refund under the paylite.audit logger so support can reconstruct disputes without engineering help.",
    acceptance_criteria: [
      { id: "AC-1", given: "a captured payment", when: "a refund is issued", then: "exactly one record is logged on paylite.audit" },
      { id: "AC-2", given: "a refund audit record", when: "support reads it", then: "it contains merchant id, payment id, refund id, amount, timestamp and reason" },
      { id: "AC-3", given: "a refund audit record", when: "support matches it to a bank statement", then: "it identifies the card used for the payment" },
      { id: "AC-4", given: "existing refund behaviour", when: "a refund is issued", then: "API responses and payment status are unchanged" },
    ],
    tasks: [
      { id: "T-1", title: "Audit logger", description: "Emit a structured record on paylite.audit for each refund." },
      { id: "T-2", title: "Card identification", description: "Include card details that let support identify the card." },
    ],
    clarifying_questions: [],
    assumptions: ["Log shipping and retention are handled by the platform team."],
    out_of_scope: ["A support UI for browsing audit records"],
    ambiguity_score: 0.31,
  },
  design: {
    summary: "Add an audit logger call at the end of refund_payment, after the refund is stored.",
    impacted_components: [
      { path: "app/refunds.py", change_type: "modify", reason: "Emit the paylite.audit record after the refund is persisted." },
    ],
    context_files: ["app/models.py", "app/payments.py"],
    untouched_components: ["app/main.py", "app/models.py"],
    design_notes: "Use logging extra= fields so the record is structured; never block the refund on logging.",
    test_strategy: "Capture paylite.audit with caplog and assert one record per refund with the expected fields.",
    risks: ["Logging card data may conflict with PCI DSS: only masked card data may be logged."],
  },
  impact_warnings: ["Payment.card_number holds the raw PAN; app/models.py exposes masked_card for display."],
  acceptance: {
    path: "tests/test_acceptance_req_102.py",
    covered_criteria: ["AC-1", "AC-2", "AC-3", "AC-4"],
    baseline: { passed: 2, failed: 4, errors: 0 },
    warning: "",
    content: "",
  },
  dev_attempt: 2,
  dev_budget: 3,
  feedback: [
    "SECURITY BLOCKER [high] card-number-logged at app/refunds.py:41: Full card number (PAN) written to the audit log. Fix this without breaking the tests (for card data use payment.masked_card; never log or return the full card number).",
  ],
  scope: { ok: true, files: ["app/refunds.py"], violations: [] },
  qa: {
    ok: true,
    dev_files: ["app/refunds.py"],
    tests: {
      passed: 31,
      failed: 0,
      errors: 0,
      skipped: 0,
      total: 31,
      ok: true,
      coverage_percent: 97.9,
      coverage_by_file: { "app/refunds.py": 100, "app/main.py": 96.4, "app/models.py": 100, "app/payments.py": 98.2 },
      cases: [
        "test_audit_record_written_once",
        "test_audit_record_fields",
        "test_audit_record_identifies_card",
        "test_refund_behaviour_unchanged",
        "test_audit_logger_name",
        "test_audit_does_not_block_refund",
      ].map((n, i) => ({ nodeid: `tests/test_acceptance_req_102.py::${n}`, outcome: "passed", duration_s: 0.01 + i * 0.002, message: "" })),
    },
    lint: { ok: true, issues: [] },
  },
  security: {
    findings: [
      blockingFinding,
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
    ],
    blocking: [blockingFinding],
    introduced_count: 1,
    triage: {
      summary:
        "The introduced finding is a true positive: the full PAN reaches log storage. payment.masked_card satisfies the support use case (last four digits) without violating PCI DSS.",
      assessments: [
        {
          rule: "card-number-logged",
          file: "app/refunds.py",
          line: 41,
          verdict: "true_positive",
          explanation: "payment.card_number is the raw PAN; logging it is a PCI DSS violation regardless of log access controls.",
        },
      ],
      additional_concerns: [
        { severity: "low", file: "app/refunds.py", line: 39, description: "Refund reason is free text and may contain customer PII." },
      ],
    },
  },
  security_fix_attempts: 1,
  attempts_log: [
    { attempt: 1, ts: "", stage: "qa", scope_ok: true, passed: 31, failed: 0, errors: 0, coverage: 97.9, lint_issues: 0 },
    { attempt: 2, ts: "", stage: "qa", scope_ok: true, passed: 31, failed: 0, errors: 0, coverage: 97.9, lint_issues: 0 },
  ],
  human_decisions: [],
};

export const req102Script: ScriptStep[] = [
  { at: 0, node: null, kind: "run_created", payload: { requirement: "REQ-102", provider: "gemini" } },
  { at: 1, node: "intake", kind: "node_start" },
  { at: 2, node: "intake", kind: "node_end", payload: { duration_ms: 1610, summary: `worktree ${REQ102_BRANCH} @ 4e1b9d02` } },
  { at: 2, node: "analyst", kind: "node_start" },
  { at: 9, node: "analyst", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 6400, input_tokens: 4980, output_tokens: 1050, repairs: 0 } },
  { at: 10, node: "analyst", kind: "node_end", payload: { duration_ms: 7720, summary: "4 criteria, 2 tasks, 0 clarifying question(s)" } },
  { at: 10, node: "architect", kind: "node_start" },
  { at: 21, node: "architect", kind: "llm_repair", payload: { provider: "gemini", repair: 1, error: "Expecting ',' delimiter: line 14 column 5" } },
  { at: 27, node: "architect", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 15200, input_tokens: 6920, output_tokens: 1230, repairs: 1 } },
  { at: 28, node: "architect", kind: "node_end", payload: { duration_ms: 17840, summary: "1 file(s): app/refunds.py" } },
  { at: 28, node: "qa_author", kind: "node_start" },
  { at: 40, node: "qa", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 11200, input_tokens: 7210, output_tokens: 2140, repairs: 0 } },
  { at: 44, node: "qa_author", kind: "node_end", payload: { duration_ms: 15900, summary: "6 tests; 4 fail before the change (expected)" } },
  { at: 44, node: "developer", kind: "node_start" },
  { at: 57, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 12400, input_tokens: 9840, output_tokens: 1320, repairs: 0 } },
  { at: 58, node: "developer", kind: "node_end", payload: { duration_ms: 13650, summary: "attempt 1: 1 file(s), 2 edit(s)" } },
  { at: 58, node: "apply_change", kind: "node_start" },
  { at: 58, node: "apply_change", kind: "node_end", payload: { duration_ms: 74, summary: "wrote 1 file(s) within approved scope" } },
  { at: 58, node: "qa_run", kind: "node_start" },
  { at: 69, node: "qa_run", kind: "node_end", payload: { duration_ms: 10820, summary: "31 passed, 0 failed, 0 errors; coverage 97.9%; 0 lint issue(s)" } },
  { at: 69, node: "security", kind: "node_start" },
  { at: 78, node: "security", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 6100, input_tokens: 5240, output_tokens: 860, repairs: 0 } },
  { at: 79, node: "security", kind: "node_end", payload: { duration_ms: 9910, summary: "1 blocking finding(s): sent back to the developer to fix" } },
  { at: 79, node: "developer", kind: "node_start" },
  { at: 92, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 12700, input_tokens: 10420, output_tokens: 1190, repairs: 0 } },
  { at: 93, node: "developer", kind: "node_end", payload: { duration_ms: 13800, summary: "attempt 2: 1 file(s), 1 edit(s)" } },
  { at: 93, node: "apply_change", kind: "node_start" },
  { at: 93, node: "apply_change", kind: "duplicate_action_skipped", payload: { action: "write_files" } },
  { at: 93, node: "apply_change", kind: "node_end", payload: { duration_ms: 61, summary: "wrote 1 file(s) within approved scope" } },
  { at: 93, node: "qa_run", kind: "node_start" },
  { at: 104, node: "qa_run", kind: "node_end", payload: { duration_ms: 10950, summary: "31 passed, 0 failed, 0 errors; coverage 97.9%; 0 lint issue(s)" } },
  { at: 104, node: "security", kind: "node_start" },
  { at: 112, node: "security", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 5900, input_tokens: 5310, output_tokens: 910, repairs: 0 } },
  { at: 113, node: "security", kind: "node_end", payload: { duration_ms: 9020, summary: "blocking finding(s) remain: escalating to a human" } },
  { at: 113, node: "escalate", kind: "node_start" },
  { at: 113, node: null, kind: "gate_opened", payload: { gate: req102EscalationGate } },
];

export const req102Diff: DiffResponse = {
  stat: { files: 1, added: 10, removed: 0 },
  diff: `diff --git a/app/refunds.py b/app/refunds.py
index 51c9d0e..c3e9a71 100644
--- a/app/refunds.py
+++ b/app/refunds.py
@@ -11,6 +11,7 @@ from app.payments import get_payment
 from app.store import new_id, store

 logger = logging.getLogger("paylite.refunds")
+audit = logging.getLogger("paylite.audit")


 def refund_payment(payment_id: str, reason: str = "") -> Refund:
@@ -27,7 +28,16 @@ def refund_payment(payment_id: str, reason: str = "") -> Refund:
     store.refunds[refund.id] = refund
     payment.status = PaymentStatus.REFUNDED
     logger.info("refund issued id=%s payment=%s amount=%s", refund.id, payment.id, refund.amount_paise)
+    audit.info(
+        "refund %s merchant=%s payment=%s amount=%s card=%s",
+        refund.id,
+        payment.merchant_id,
+        payment.id,
+        refund.amount_paise,
+        payment.card_number,
+        extra={"reason": reason, "refunded_at": refund.created_at.isoformat()},
+    )
     notify(
         payment.merchant_id,
         "refund.created",
`,
};

export const req102UsageByAgent: UsageByAgent[] = [
  { agent: "analyst", provider: "gemini", model: "gemini-2.5-flash", calls: 1, input_tokens: 4980, output_tokens: 1050, latency_ms: 6400, cost_usd: 0.0043, cache_hits: 0, failures: 0 },
  { agent: "architect", provider: "gemini", model: "gemini-2.5-pro", calls: 2, input_tokens: 13410, output_tokens: 2390, latency_ms: 24300, cost_usd: 0.0407, cache_hits: 0, failures: 0 },
  { agent: "qa", provider: "gemini", model: "gemini-2.5-pro", calls: 1, input_tokens: 7210, output_tokens: 2140, latency_ms: 11200, cost_usd: 0.0304, cache_hits: 0, failures: 0 },
  { agent: "developer", provider: "gemini", model: "gemini-2.5-pro", calls: 2, input_tokens: 20260, output_tokens: 2510, latency_ms: 25100, cost_usd: 0.0504, cache_hits: 0, failures: 0 },
  { agent: "security", provider: "gemini", model: "gemini-2.5-flash", calls: 2, input_tokens: 10550, output_tokens: 1770, latency_ms: 12000, cost_usd: 0.0075, cache_hits: 0, failures: 0 },
];
