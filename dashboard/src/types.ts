// Types mirroring docs/API.md (the HTTP contract with the FastAPI backend).

export type ProviderName = "gemini" | "ollama";

export type RunStatus =
  | "created"
  | "running"
  | "awaiting_human"
  | "paused_provider_unavailable"
  | "failed"
  | "completed"
  | "rejected"
  | "aborted";

export interface Health {
  status: string;
  version: string;
}

export interface ProviderInfo {
  name: ProviderName;
  available: boolean;
  detail: string;
  models: Record<string, string>;
}

export interface ProvidersResponse {
  default: ProviderName;
  failover: boolean;
  providers: ProviderInfo[];
}

export interface PolicyResponse {
  sha256: string;
  policy: Record<string, unknown>;
}

export interface RequirementInfo {
  path: string;
  id: string;
  title: string;
  priority: string;
  body: string;
}

export type NodeKind = "system" | "agent" | "human" | "guard";

export interface GraphNodeDef {
  id: string;
  label: string;
  kind: NodeKind;
}

export interface GraphEdgeDef {
  source: string;
  target: string;
  label?: string | null;
}

export interface GraphResponse {
  nodes: GraphNodeDef[];
  edges: GraphEdgeDef[];
}

export interface Scores {
  golden_pass_rate: number | null;
  rubric_percent: number | null;
  overall: number | null;
}

export interface ClarificationGate {
  gate: "clarification";
  questions: string[];
  requirement: string;
  assumptions: string[];
}

export type Recommendation = "approve" | "request_changes" | "reject";

export interface ReleaseApprovalGate {
  gate: "release_approval";
  headline: string;
  recommendation: Recommendation;
  reason: string;
  branch: string;
  commit: string;
  facts: Record<string, unknown>;
  scores: Scores | null;
  known_risks: string[];
}

export type EscalationReason = "malformed_output" | "retry_budget_exhausted" | "security_block" | "design_deadlock";
export type EscalationOption = "retry" | "override" | "abort";

export interface EscalationGate {
  gate: "escalation";
  reason: EscalationReason;
  node: string;
  details: string;
  resume_node: string;
  options: EscalationOption[];
}

export type Gate = ClarificationGate | ReleaseApprovalGate | EscalationGate;

export interface RunSummary {
  id: string;
  requirement_id: string;
  title: string;
  provider: ProviderName;
  status: RunStatus;
  stage: string | null;
  outcome: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  gate: Gate | null;
  /** true for created/running runs that nothing is executing any more */
  stalled?: boolean;
}

// ---- decisions sent to POST /resume
export interface ClarificationDecision {
  answers: string[];
  actor: string;
}
export interface ReleaseDecision {
  decision: Recommendation;
  comment: string;
  actor: string;
}
export interface EscalationDecision {
  decision: EscalationOption;
  comment: string;
  actor: string;
}
export type Decision = ClarificationDecision | ReleaseDecision | EscalationDecision;

export interface CancelBody {
  actor?: string;
}

export interface ResumeBody {
  decision: Decision | null;
  provider: ProviderName | null;
}

// ---- run state (LangGraph checkpoint values)
export interface AcceptanceCriterion {
  id: string;
  given: string;
  when: string;
  then: string;
}

export interface Analysis {
  summary: string;
  acceptance_criteria: AcceptanceCriterion[];
  tasks: { id: string; title: string; description: string }[];
  clarifying_questions: string[];
  assumptions: string[];
  out_of_scope: string[];
  ambiguity_score: number;
}

export interface ImpactedComponent {
  path: string;
  change_type: "modify" | "create";
  reason: string;
}

export interface Design {
  summary: string;
  impacted_components: ImpactedComponent[];
  context_files: string[];
  untouched_components: string[];
  design_notes: string;
  test_strategy: string;
  risks: string[];
}

export interface Acceptance {
  path: string;
  covered_criteria: string[];
  baseline: { passed: number; failed: number; errors: number };
  warning: string;
  content: string;
}

export interface ScopeViolation {
  path: string;
  rule: string;
  reason: string;
}

export interface Scope {
  ok: boolean;
  files?: string[];
  violations: ScopeViolation[];
}

export interface TestCase {
  nodeid: string;
  outcome: string;
  duration_s: number;
  message: string;
}

export interface TestReport {
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  total: number;
  ok: boolean;
  coverage_percent: number | null;
  coverage_by_file: Record<string, number>;
  cases: TestCase[];
}

export interface LintIssue {
  file: string;
  line: number;
  code: string;
  message: string;
}

export interface QAResult {
  ok: boolean;
  dev_files: string[];
  tests: TestReport;
  lint: { ok: boolean; issues: LintIssue[] };
}

export type Severity = "critical" | "high" | "medium" | "low";

export interface Finding {
  rule: string;
  severity: Severity;
  file: string;
  line: number;
  message: string;
  evidence: string;
  source: "custom" | "bandit";
  introduced: boolean;
}

export interface SecurityTriage {
  summary: string;
  assessments: { rule: string; file: string; line: number; verdict: string; explanation: string }[];
  additional_concerns: { severity: string; file: string; line: number; description: string }[];
}

export interface SecurityResult {
  findings: Finding[];
  blocking: Finding[];
  introduced_count: number;
  triage: SecurityTriage | null;
}

export interface AttemptLogEntry {
  attempt: number;
  ts: string;
  stage: "scope" | "qa";
  scope_ok: boolean;
  passed?: number;
  failed?: number;
  errors?: number;
  coverage?: number;
  lint_issues?: number;
  violations?: ScopeViolation[];
}

export interface Evaluation {
  golden: { available: boolean; passed: number; total: number; failures: string[] };
  rubric: { items: { criterion: string; score: number; justification: string }[]; overall_comment: string } | null;
  scores: Scores;
  reference_files: string[];
  agent_files: string[];
}

export interface ReleaseEvidence {
  headline: string;
  change_summary: string;
  recommendation: string;
  recommendation_reason: string;
  criteria_evidence: { criterion_id: string; status: "met" | "not_met" | "unverified"; evidence: string }[];
  known_risks: string[];
  rollback_plan: string;
  facts: Record<string, unknown>;
  diff_stat: DiffStat;
  branch: string;
  commit: string;
}

export interface HumanDecision {
  gate: string;
  decision?: string;
  actor: string;
  comment?: string;
  ts: string;
  reason?: string;
}

export interface RunState {
  analysis?: Analysis;
  clarifications?: { question: string; answer: string }[];
  design?: Design;
  impact_warnings?: string[];
  acceptance?: Acceptance;
  dev_attempt?: number;
  dev_budget?: number;
  feedback?: string[];
  scope?: Scope;
  qa?: QAResult;
  security?: SecurityResult;
  security_fix_attempts?: number;
  security_override?: { actor: string; justification: string; ts: string };
  attempts_log?: AttemptLogEntry[];
  evaluation?: Evaluation;
  release?: ReleaseEvidence;
  human_decisions?: HumanDecision[];
  outcome?: "merged" | "rejected" | "aborted";
}

export interface RequirementRef {
  id: string;
  title: string;
  body: string;
  path: string;
  priority: string;
}

export interface RunDetail extends RunSummary {
  requirement: RequirementRef;
  state: RunState;
  next: string[];
}

export interface RunEvent {
  id: number;
  run_id: string;
  ts: string;
  node: string | null;
  kind: string;
  payload: Record<string, unknown>;
}

export interface DiffStat {
  files: number;
  added: number;
  removed: number;
}

export interface DiffResponse {
  diff: string;
  stat: DiffStat;
}

export interface UsageNumbers {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: number;
  cache_hits: number;
  failures: number;
}

export interface UsageByAgent extends UsageNumbers {
  agent: string;
  provider: string;
  model: string;
}

export type LLMCallStatus = "ok" | "repaired" | "cached" | "invalid_output" | "provider_error";

export interface LLMCall {
  id: number;
  ts: string;
  agent: string;
  provider: string;
  model: string;
  tier: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: number;
  cached: 0 | 1;
  attempt: number;
  status: LLMCallStatus;
  error: string | null;
}

export interface UsageResponse {
  by_agent: UsageByAgent[];
  totals: Partial<UsageNumbers>;
  calls: LLMCall[];
}

export interface AuditEntry {
  seq: number;
  ts: string;
  actor: string;
  action: string;
  details: Record<string, unknown> | string | null;
  prev_hash: string;
  hash: string;
}

export interface AuditVerification {
  ok: boolean;
  entries: number;
  broken_at_seq: number | null;
  reason: string;
}

export interface AuditResponse {
  entries: AuditEntry[];
  verification: AuditVerification;
}

export interface Approval {
  ts: string;
  gate: string;
  decision: string;
  actor: string;
  comment: string;
}

export interface CreateRunCommon {
  provider: ProviderName;
  cache: boolean;
}

/** POST /api/runs: exactly one of requirement_text / requirement_path. */
export type CreateRunBody =
  | (CreateRunCommon & { requirement_path: string; requirement_text?: never; title?: never })
  | (CreateRunCommon & { requirement_text: string; title?: string; requirement_path?: never });

// ---- live agent output (event kinds llm_delta / agent_output)

/** Agent names used by llm_* and agent_output events. */
export type AgentName = "analyst" | "architect" | "qa" | "developer" | "security" | "release" | "evaluator";

export interface LlmDeltaPayload {
  call: string | number;
  provider: string;
  model: string;
  text: string;
}

export interface QAAgentOutput {
  content: string;
  covered_criteria: string[];
}

export interface DeveloperEdit {
  find: string;
  replace: string;
}

export interface DeveloperFile {
  path: string;
  edits?: DeveloperEdit[] | null;
  content?: string | null;
}

export interface DeveloperAgentOutput {
  files: DeveloperFile[];
  rationale: string;
  design_objection?: string | null;
}

export interface RubricOutput {
  items: { criterion: string; score: number; justification: string }[];
  overall_comment: string;
}

export interface AgentOutputPayload {
  agent: AgentName | string;
  output: unknown;
}
