// Scripted run for free-text requirements in mock mode: every agent streams llm_delta text,
// then emits agent_output. Stops at clarification, retries once after a failing test run,
// and stops again at release approval.

import type { ClarificationGate, ProviderName, ReleaseApprovalGate, RunState } from "../types";
import { req101ApprovalGate, req101ClarificationGate, req101State, type ScriptStep } from "./req101";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

const TEST_CODE = `"""Acceptance tests for partial refunds (written before the change)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.payments import capture_payment, create_payment

client = TestClient(app)


@pytest.fixture
def captured():
    payment = create_payment(merchant_id="m_1", amount_paise=10_000, card="4111111111111111")
    return capture_payment(payment.id)


def refund(payment_id: str, **body):
    return client.post(f"/payments/{payment_id}/refunds", json=body)


def test_partial_refund_sets_partially_refunded(captured):
    res = refund(captured.id, amount_paise=4_000)
    assert res.status_code == 201
    body = client.get(f"/payments/{captured.id}").json()
    assert body["status"] == "PARTIALLY_REFUNDED"
    assert body["refundable_paise"] == 6_000


def test_second_partial_refund_completes_payment(captured):
    refund(captured.id, amount_paise=4_000)
    refund(captured.id, amount_paise=6_000)
    body = client.get(f"/payments/{captured.id}").json()
    assert body["status"] == "REFUNDED"
    assert body["refundable_paise"] == 0


def test_refund_exceeding_remaining_is_rejected(captured):
    refund(captured.id, amount_paise=4_000)
    res = refund(captured.id, amount_paise=7_000)
    assert res.status_code == 400
    assert res.json()["code"] == "REFUND_EXCEEDS_REMAINING"


@pytest.mark.parametrize("amount", [0, -100, 12.5])
def test_invalid_amount(captured, amount):
    res = refund(captured.id, amount_paise=amount)
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_AMOUNT"


def test_omitted_amount_refunds_remaining(captured):
    refund(captured.id, amount_paise=4_000)
    refund(captured.id)
    assert client.get(f"/payments/{captured.id}").json()["status"] == "REFUNDED"


def test_get_payment_exposes_refund_totals(captured):
    refund(captured.id, amount_paise=2_500)
    body = client.get(f"/payments/{captured.id}").json()
    assert body["refunded_paise"] == 2_500
    assert body["refundable_paise"] == 7_500
`;

const DEV_ATTEMPT_1 = {
  rationale:
    "Totals are derived from stored refunds, so history and totals cannot disagree. Validation runs before any mutation; the API passes amount_paise straight through.",
  design_objection: null,
  files: [
    {
      path: "app/models.py",
      edits: [{ find: '    CAPTURED = "CAPTURED"\n    REFUNDED = "REFUNDED"', replace: '    CAPTURED = "CAPTURED"\n    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"\n    REFUNDED = "REFUNDED"' }],
      content: null,
    },
    {
      path: "app/refunds.py",
      edits: [
        {
          find: "def refund_payment(payment_id: str, reason: str = \"\") -> Refund:\n    payment = get_payment(payment_id)\n    if payment.status != PaymentStatus.CAPTURED:\n        raise ConflictError(\"PAYMENT_NOT_REFUNDABLE\")",
          replace:
            "def refund_payment(payment_id: str, amount_paise: int | None = None, reason: str = \"\") -> Refund:\n    payment = get_payment(payment_id)\n    if payment.status not in REFUNDABLE:\n        raise ConflictError(\"PAYMENT_NOT_REFUNDABLE\")\n    remaining = refundable_amount(payment)\n    amount = remaining if amount_paise is None else amount_paise\n    if not isinstance(amount, int) or amount <= 0:\n        raise ValidationError(\"INVALID_AMOUNT\")",
        },
      ],
      content: null,
    },
    {
      path: "app/main.py",
      edits: [{ find: 'class RefundIn(BaseModel):\n    reason: str = ""', replace: 'class RefundIn(BaseModel):\n    reason: str = ""\n    amount_paise: int | None = None' }],
      content: null,
    },
  ],
};

const DEV_ATTEMPT_2 = {
  rationale:
    "Two tests failed: the remaining-amount limit was never enforced, and payment_out() did not expose the new totals. Both are fixed without touching other files.",
  design_objection: null,
  files: [
    {
      path: "app/refunds.py",
      edits: [
        {
          find: "    if not isinstance(amount, int) or amount <= 0:\n        raise ValidationError(\"INVALID_AMOUNT\")",
          replace:
            "    if not isinstance(amount, int) or amount <= 0:\n        raise ValidationError(\"INVALID_AMOUNT\")\n    if amount > remaining:\n        raise ValidationError(\"REFUND_EXCEEDS_REMAINING\")",
        },
      ],
      content: null,
    },
    {
      path: "app/main.py",
      edits: [
        {
          find: '        "status": payment.status.value,\n    }',
          replace: '        "status": payment.status.value,\n        "refunded_paise": refunded_total(payment.id),\n        "refundable_paise": refundable_amount(payment),\n    }',
        },
      ],
      content: null,
    },
  ],
};

let callSeq = 0;

function chunk(text: string): string[] {
  const size = Math.max(10, Math.ceil(text.length / 90));
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}

type Edit = { find?: string; replace?: string };
type FileOut = { path: string; edits?: Edit[]; content?: string };

/** What the model actually writes: QA and Developer answer in plain text (fenced code, SEARCH/REPLACE
 *  blocks), the other agents in JSON. Mirrors the backend's two protocols. */
function rawText(agent: string, output: unknown): string {
  const o = output as Record<string, unknown>;
  if (agent === "qa" && typeof o.content === "string") {
    const covers = Array.isArray(o.covered_criteria) ? (o.covered_criteria as string[]).join(", ") : "";
    return `COVERS: ${covers}\n\`\`\`python\n${o.content}\n\`\`\`\n`;
  }
  if (agent === "developer" && Array.isArray(o.files)) {
    const parts = [`RATIONALE: ${String(o.rationale ?? "")}`];
    for (const f of o.files as FileOut[]) {
      parts.push(`FILE: ${f.path}`);
      for (const e of f.edits ?? []) parts.push(`<<<<<<< SEARCH\n${e.find ?? ""}\n=======\n${e.replace ?? ""}\n>>>>>>> REPLACE`);
      if (f.content) parts.push(`<<<<<<< CONTENT\n${f.content}\n>>>>>>> END`);
    }
    return parts.join("\n");
  }
  return JSON.stringify(output, null, 2);
}

/** llm_delta stream for one call, followed by llm_call and (optionally) agent_output. */
function agentCall(agent: string, provider: string, model: string, output: unknown, at: number, opts: { emitOutput?: boolean; partial?: number } = {}): ScriptStep[] {
  const call = `c${++callSeq}`;
  const text = rawText(agent, output);
  const parts = chunk(opts.partial ? text.slice(0, opts.partial) : text);
  const steps: ScriptStep[] = parts.map((t) => ({ at, node: agent, kind: "llm_delta", payload: { call, provider, model, text: t }, gap: 32 }));
  if (opts.partial) return steps;
  steps.push({
    at,
    node: agent,
    kind: "llm_call",
    payload: { provider, model, latency_ms: 4000 + parts.length * 35, input_tokens: 4000 + text.length, output_tokens: Math.round(text.length / 3.6), repairs: 0 },
  });
  if (opts.emitOutput !== false) steps.push({ at, node: agent, kind: "agent_output", payload: { agent, output } });
  return steps;
}

const start = (node: string, at: number): ScriptStep => ({ at, node, kind: "node_start" });
const end = (node: string, at: number, summary: string, duration_ms = 1200): ScriptStep => ({ at, node, kind: "node_end", payload: { duration_ms, summary } });

export interface ConsoleRunFixture {
  script: ScriptStep[];
  finalState: RunState;
  clarification: ClarificationGate;
  approval: ReleaseApprovalGate;
}

export function buildConsoleRun(reqId: string, title: string, provider: ProviderName, runId: string): ConsoleRunFixture {
  const light = provider === "ollama" ? "llama3.1:8b" : "gemini-2.5-flash";
  const code = provider === "ollama" ? "qwen2.5-coder:14b" : "gemini-2.5-pro";
  const other = provider === "ollama" ? "gemini" : "ollama";
  const otherCode = provider === "ollama" ? "gemini-2.5-pro" : "qwen2.5-coder:14b";
  const branch = `forgeline/${reqId.toLowerCase()}-${runId.replace(/^run_/, "")}`;

  const base = clone(req101State);
  const analysis1 = { ...clone(base.analysis!), clarifying_questions: clone(req101ClarificationGate.questions), ambiguity_score: 0.48 };
  const analysis2 = clone(base.analysis!);
  const qaOutput = { content: TEST_CODE, covered_criteria: base.acceptance!.covered_criteria };
  const release = { ...clone(base.release!), branch, headline: title || base.release!.headline };

  const clarification: ClarificationGate = { ...clone(req101ClarificationGate), requirement: `${reqId} ${title}` };
  const approval: ReleaseApprovalGate = {
    ...clone(req101ApprovalGate),
    headline: release.headline,
    branch,
    facts: { ...clone(req101ApprovalGate.facts), developer_attempts: 2, tests: "41 passed, 0 failed, 0 errors" },
  };
  release.facts = approval.facts;

  const finalState: RunState = {
    ...base,
    acceptance: { ...base.acceptance!, content: TEST_CODE },
    dev_attempt: 2,
    dev_budget: 3,
    scope: { ok: true, files: ["app/models.py", "app/refunds.py", "app/main.py"], violations: [] },
    attempts_log: [
      { attempt: 1, ts: "", stage: "qa", scope_ok: true, passed: 39, failed: 2, errors: 0, coverage: 96.1, lint_issues: 0 },
      { attempt: 2, ts: "", stage: "qa", scope_ok: true, passed: 41, failed: 0, errors: 0, coverage: 98.7, lint_issues: 0 },
    ],
    release,
    human_decisions: [],
  };

  const script: ScriptStep[] = [
    { at: 0, node: null, kind: "run_created", payload: { requirement: reqId, provider } },
    start("intake", 1),
    end("intake", 2, `worktree ${branch}`, 1400),
    start("analyst", 2),
    ...agentCall("analyst", provider, light, analysis1, 3),
    end("analyst", 10, `${analysis1.acceptance_criteria.length} criteria, ${analysis1.tasks.length} tasks, 2 clarifying question(s)`, 7600),
    start("clarify", 10),
    { at: 10, node: null, kind: "gate_opened", payload: { gate: clarification } },
    // ---- resumes here after the clarification answers
    { at: 60, node: null, kind: "gate_answered", payload: { gate: "clarification", decision: "answered", actor: "reviewer" } },
    start("clarify", 61),
    end("clarify", 61, "2 answer(s)", 40),
    start("analyst", 61),
    ...agentCall("analyst", provider, light, analysis2, 62),
    end("analyst", 69, `${analysis2.acceptance_criteria.length} criteria, ${analysis2.tasks.length} tasks, 0 clarifying question(s)`, 7100),
    start("architect", 69),
    ...agentCall("architect", provider, light, base.design, 70, { partial: 260 }),
    { at: 72, node: "architect", kind: "llm_retry", payload: { provider, delay_s: 2, error: "503 UNAVAILABLE: the model is overloaded" } },
    { at: 75, node: "architect", kind: "llm_failover", payload: { from: provider, to: other, error: "503 UNAVAILABLE" } },
    ...agentCall("architect", other, otherCode, base.design, 76),
    end("architect", 90, `${base.design!.impacted_components.length} file(s): ${base.design!.impacted_components.map((c) => c.path).join(", ")}`, 19800),
    start("qa_author", 90),
    ...agentCall("qa", provider, code, qaOutput, 91),
    end("qa_author", 104, "7 tests; 6 fail before the change (expected)", 13400),
    start("developer", 104),
    ...agentCall("developer", provider, code, DEV_ATTEMPT_1, 105),
    end("developer", 118, "attempt 1: 3 file(s), 3 edit(s)", 13900),
    start("apply_change", 118),
    end("apply_change", 118, "wrote 3 file(s) within approved scope", 90),
    start("qa_run", 118),
    end("qa_run", 130, "39 passed, 2 failed, 0 errors; coverage 96.1%; 0 lint issue(s)", 11800),
    start("developer", 130),
    ...agentCall("developer", provider, code, DEV_ATTEMPT_2, 131),
    end("developer", 143, "attempt 2: 2 file(s), 2 edit(s)", 12600),
    start("apply_change", 143),
    end("apply_change", 143, "wrote 2 file(s) within approved scope", 85),
    start("qa_run", 143),
    end("qa_run", 155, "41 passed, 0 failed, 0 errors; coverage 98.7%; 0 lint issue(s)", 12000),
    start("security", 155),
    ...agentCall("security", provider, light, base.security!.triage, 156),
    end("security", 164, `${base.security!.findings.length} finding(s), 0 blocking`, 8800),
    start("evaluate", 164),
    ...agentCall("evaluator", provider, light, base.evaluation!.rubric, 165),
    end("evaluate", 176, `golden ${base.evaluation!.golden.passed}/${base.evaluation!.golden.total}; overall ${base.evaluation!.scores.overall}`, 11600),
    start("release", 176),
    ...agentCall(
      "release",
      provider,
      light,
      {
        headline: release.headline,
        change_summary: release.change_summary,
        recommendation: release.recommendation,
        recommendation_reason: release.recommendation_reason,
        criteria_evidence: release.criteria_evidence,
        known_risks: release.known_risks,
        rollback_plan: release.rollback_plan,
      },
      177,
    ),
    end("release", 188, `recommendation: ${release.recommendation}; commit ${release.commit.slice(0, 8)}`, 10900),
    start("approval", 188),
    { at: 188, node: null, kind: "gate_opened", payload: { gate: approval } },
  ];

  return { script, finalState, clarification, approval };
}
