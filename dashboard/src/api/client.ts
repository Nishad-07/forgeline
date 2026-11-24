// Typed fetch wrapper for the Forgeline HTTP API (docs/API.md).
// When VITE_MOCK=1 every call is answered by the in-memory fixtures in src/mocks/.

import type {
  Approval,
  AuditResponse,
  CancelBody,
  CreateRunBody,
  DiffResponse,
  GraphResponse,
  Health,
  PolicyResponse,
  ProvidersResponse,
  RequirementInfo,
  ResumeBody,
  RunDetail,
  RunEvent,
  RunSummary,
  UsageResponse,
} from "../types";

export const IS_MOCK = import.meta.env.VITE_MOCK === "1";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

type Method = "GET" | "POST";

function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      // FastAPI validation errors: [{loc, msg, type}]
      return d
        .map((x) => (x && typeof x === "object" && "msg" in x ? String((x as { msg: unknown }).msg) : JSON.stringify(x)))
        .join("; ");
    }
    return JSON.stringify(d);
  }
  return fallback;
}

export async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  if (IS_MOCK) {
    const mock = await import("../mocks/mockApi");
    return mock.mockRequest<T>(method, path, body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Forgeline API.");
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, extractDetail(data, typeof data === "string" && data ? data : res.statusText));
  }
  return data as T;
}

const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("GET", "/health"),
  providers: () => request<ProvidersResponse>("GET", "/providers"),
  policy: () => request<PolicyResponse>("GET", "/policy"),
  requirements: () => request<RequirementInfo[]>("GET", "/requirements"),
  graph: () => request<GraphResponse>("GET", "/graph"),
  runs: () => request<RunSummary[]>("GET", "/runs"),
  createRun: (body: CreateRunBody) => request<RunSummary>("POST", "/runs", body),
  run: (id: string) => request<RunDetail>("GET", `/runs/${enc(id)}`),
  events: (id: string, after = 0) => request<RunEvent[]>("GET", `/runs/${enc(id)}/events?after=${after}`),
  resume: (id: string, body: ResumeBody) => request<RunSummary>("POST", `/runs/${enc(id)}/resume`, body),
  cancelRun: (id: string, actor?: string) =>
    request<RunSummary>("POST", `/runs/${enc(id)}/cancel`, (actor?.trim() ? { actor: actor.trim() } : {}) satisfies CancelBody),
  diff: (id: string) => request<DiffResponse>("GET", `/runs/${enc(id)}/diff`),
  usage: (id: string) => request<UsageResponse>("GET", `/runs/${enc(id)}/usage`),
  audit: (id: string) => request<AuditResponse>("GET", `/runs/${enc(id)}/audit`),
  approvals: (id: string) => request<Approval[]>("GET", `/runs/${enc(id)}/approvals`),
};

export function streamUrl(id: string, after: number): string {
  return `/api/runs/${enc(id)}/stream?after=${after}`;
}

/** Every event kind the backend may send as an SSE `event:` name (docs/API.md + a few extras). */
export const SSE_EVENT_KINDS = [
  "run_created",
  "node_start",
  "node_end",
  "node_error",
  "llm_call",
  "llm_delta",
  "agent_output",
  "llm_retry",
  "llm_repair",
  "llm_failover",
  "llm_provider_failed",
  "agent_output_rejected",
  "agent_escalation",
  "scope_violation",
  "repeated_action_blocked",
  "duplicate_action_skipped",
  "qa_tests_rejected",
  "gate_opened",
  "gate_answered",
  "provider_switched",
  "run_paused",
  "run_failed",
  "run_finished",
  "run_cancelled",
  "triage_unavailable",
  "rubric_unavailable",
] as const;
