// Static fixtures shared by all mock runs: providers, policy, requirements, graph.

import type { GraphResponse, PolicyResponse, ProvidersResponse, RequirementInfo } from "../types";

export const providers: ProvidersResponse = {
  default: "gemini",
  failover: true,
  providers: [
    {
      name: "gemini",
      available: true,
      detail: "API key present",
      models: { light: "gemini-2.5-flash", code: "gemini-2.5-pro" },
    },
    {
      name: "ollama",
      available: true,
      detail: "http://localhost:11434 reachable (3 models pulled)",
      models: { light: "llama3.1:8b", code: "qwen2.5-coder:14b" },
    },
  ],
};

export const policy: PolicyResponse = {
  sha256: "b3f1c07a9d2e48f6a1c5e9037d4b2a816fe0c3d95a7b1e2f4c6d8a0b9e1f3a57",
  policy: {
    version: 1,
    autonomy: {
      max_dev_attempts: 3,
      max_reconcile_rounds: 1,
      max_repair_attempts: 2,
      security_autofix_attempts: 1,
      max_request_changes: 2,
    },
    human_required_for: [
      "clarification",
      "merge",
      "deploy",
      "security_override",
      "design_deadlock",
      "retry_budget_exhausted",
    ],
    security: { block_on: ["critical", "high"], scan_scope: "changed_lines" },
    scope: {
      protected_paths: [".git/**", ".github/**", "tests/golden/**", "pyproject.toml", "**/.env"],
      max_files_changed: 6,
      max_file_bytes: 60000,
    },
    sandbox: { command_timeout_s: 120, allowed_commands: ["pytest", "ruff", "bandit"] },
    llm: { failover: true, analyst_ambiguity_threshold: 0.5 },
  },
};

export const requirements: RequirementInfo[] = [
  {
    path: "requirements/partial_refund.md",
    id: "REQ-101",
    title: "Partial refunds",
    priority: "high",
    body: `# Partial refunds

## Problem
Merchants can only refund the full amount of a payment. A café that over-charged a customer by ₹40 on a ₹100 order has to refund everything and ask the customer to pay again. Merchants want to refund only part of a captured payment, possibly several times.

## Requirement
A merchant can issue one or more partial refunds against a captured payment. The total refunded across all refunds must never exceed the captured amount. The full history of refunds for a payment must remain available.

## API contract
- \`POST /payments/{payment_id}/refunds\` accepts an optional \`amount_paise\` (integer) next to the existing \`reason\`.
  - If \`amount_paise\` is omitted, refund the **entire remaining refundable amount**.
  - \`amount_paise\` must be a positive integer, otherwise respond \`400\` with error code \`INVALID_AMOUNT\`.
  - If \`amount_paise\` is greater than the remaining refundable amount, respond \`400\` with \`REFUND_EXCEEDS_REMAINING\`.
- \`GET /payments/{payment_id}\` additionally returns \`refunded_paise\` and \`refundable_paise\`.
- Payment status:
  - \`PARTIALLY_REFUNDED\` after a refund that leaves some amount refundable.
  - \`REFUNDED\` once the total refunded equals the captured amount.
- Refunds remain impossible on payments that are not captured (\`409\`, \`PAYMENT_NOT_REFUNDABLE\`).

## Not decided yet
The product team has not decided whether merchants must be notified for every partial refund or only when the payment becomes fully refunded, or whether a refund reason should be mandatory.

## Out of scope
Multi-currency, refunds to a different card, asynchronous settlement with the card network.`,
  },
  {
    path: "requirements/refund_audit_log.md",
    id: "REQ-102",
    title: "Refund audit logging for support",
    priority: "medium",
    body: `# Refund audit logging

## Problem
When a customer disputes a refund, the support team cannot reconstruct what happened. They have to ask engineering to search the database.

## Requirement
Every refund must write an audit log entry with all the payment details support needs to investigate a dispute: which merchant, which payment, how much was refunded, when, why, and which card the payment was made with, so support can match it to the customer's bank statement.

## Acceptance hints
- One audit entry per refund, written through Python's standard \`logging\` module under the logger name \`paylite.audit\`.
- Existing behaviour of refunds must not change.`,
  },
];

export const graph: GraphResponse = {
  nodes: [
    { id: "intake", label: "Intake", kind: "system" },
    { id: "analyst", label: "Analyst", kind: "agent" },
    { id: "clarify", label: "Clarification", kind: "human" },
    { id: "architect", label: "Architect", kind: "agent" },
    { id: "reconcile", label: "Reconcile", kind: "system" },
    { id: "qa_author", label: "QA author", kind: "agent" },
    { id: "developer", label: "Developer", kind: "agent" },
    { id: "apply_change", label: "Scope guard", kind: "guard" },
    { id: "qa_run", label: "Tests + lint", kind: "agent" },
    { id: "security", label: "Security", kind: "agent" },
    { id: "evaluate", label: "Evaluator", kind: "agent" },
    { id: "release", label: "Release", kind: "agent" },
    { id: "approval", label: "Release approval", kind: "human" },
    { id: "merge_deploy", label: "Merge + deploy", kind: "system" },
    { id: "escalate", label: "Escalation", kind: "human" },
    { id: "close", label: "Close", kind: "system" },
  ],
  edges: [
    { source: "intake", target: "analyst" },
    { source: "analyst", target: "clarify", label: "questions" },
    { source: "analyst", target: "architect" },
    { source: "analyst", target: "escalate" },
    { source: "clarify", target: "analyst", label: "answers" },
    { source: "architect", target: "qa_author" },
    { source: "architect", target: "developer", label: "tests exist" },
    { source: "architect", target: "escalate" },
    { source: "qa_author", target: "developer" },
    { source: "qa_author", target: "escalate" },
    { source: "developer", target: "apply_change" },
    { source: "developer", target: "reconcile", label: "objection" },
    { source: "developer", target: "escalate" },
    { source: "reconcile", target: "architect", label: "revise" },
    { source: "reconcile", target: "escalate", label: "deadlock" },
    { source: "apply_change", target: "qa_run" },
    { source: "apply_change", target: "developer", label: "rejected" },
    { source: "apply_change", target: "escalate" },
    { source: "qa_run", target: "security" },
    { source: "qa_run", target: "developer", label: "failing" },
    { source: "qa_run", target: "escalate" },
    { source: "security", target: "evaluate" },
    { source: "security", target: "developer", label: "blocking" },
    { source: "security", target: "escalate" },
    { source: "evaluate", target: "release" },
    { source: "release", target: "approval" },
    { source: "approval", target: "merge_deploy", label: "approve" },
    { source: "approval", target: "developer", label: "changes" },
    { source: "approval", target: "close", label: "reject" },
    { source: "escalate", target: "developer", label: "retry" },
    { source: "escalate", target: "evaluate", label: "override" },
    { source: "escalate", target: "close", label: "abort" },
  ],
};
