// Derives per-node execution state for the workflow graph from the event log.

import type { Gate, RunEvent, RunSummary } from "../types";

export type NodeRunState = "pending" | "running" | "done" | "failed" | "waiting" | "paused";

export interface NodeInfo {
  state: NodeRunState;
  runs: number;
  lastSummary?: string;
  lastDurationMs?: number;
  lastError?: string;
}

export interface GraphProgress {
  nodes: Record<string, NodeInfo>;
  /** "source->target" -> number of times the workflow moved along that edge */
  edges: Record<string, number>;
  lastEdge: string | null;
}

export const GATE_NODE: Record<Gate["gate"], string> = {
  clarification: "clarify",
  release_approval: "approval",
  escalation: "escalate",
};

export function deriveProgress(events: RunEvent[], run: Pick<RunSummary, "status" | "gate" | "stage"> | null, next: string[] = []): GraphProgress {
  const nodes: Record<string, NodeInfo> = {};
  const edges: Record<string, number> = {};
  let prevNode: string | null = null;
  let lastEdge: string | null = null;

  const get = (id: string) => (nodes[id] ??= { state: "pending", runs: 0 });

  const enter = (id: string) => {
    const info = get(id);
    const open = info.state === "running" || info.state === "waiting" || info.state === "paused";
    if (!open) info.runs += 1;
    info.state = "running";
    if (prevNode && prevNode !== id) {
      const key = `${prevNode}->${id}`;
      edges[key] = (edges[key] ?? 0) + 1;
      lastEdge = key;
    }
    prevNode = id;
  };

  for (const ev of events) {
    const node = ev.node;
    switch (ev.kind) {
      case "node_start":
        if (node) enter(node);
        break;
      case "node_end":
        if (node) {
          const info = get(node);
          info.state = "done";
          info.lastSummary = typeof ev.payload.summary === "string" ? ev.payload.summary : info.lastSummary;
          info.lastDurationMs = typeof ev.payload.duration_ms === "number" ? ev.payload.duration_ms : info.lastDurationMs;
        }
        break;
      case "node_error":
        if (node) {
          const info = get(node);
          info.state = "failed";
          info.lastError = String(ev.payload.error ?? "");
        }
        break;
      case "gate_opened": {
        const gate = ev.payload.gate as Gate | undefined;
        const id = gate ? GATE_NODE[gate.gate] : null;
        if (id) {
          if (get(id).state !== "running") enter(id);
          get(id).state = "waiting";
        }
        break;
      }
      case "run_paused":
      case "run_failed":
        for (const info of Object.values(nodes)) {
          if (info.state === "running") info.state = ev.kind === "run_paused" ? "paused" : "failed";
        }
        break;
      default:
        break;
    }
  }

  if (run) {
    if (run.status === "awaiting_human") {
      const ids = run.gate ? [GATE_NODE[run.gate.gate]] : next;
      ids.forEach((id) => {
        if (id) get(id).state = "waiting";
      });
    } else if (run.status === "paused_provider_unavailable" || run.status === "failed") {
      const target = run.stage ?? next[0];
      if (target && get(target).state !== "done") get(target).state = run.status === "failed" ? "failed" : "paused";
    } else if (["completed", "rejected", "aborted"].includes(run.status)) {
      for (const info of Object.values(nodes)) if (info.state === "running" || info.state === "waiting") info.state = "done";
    }
  }

  return { nodes, edges, lastEdge };
}
