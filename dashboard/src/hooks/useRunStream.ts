import { useEffect, useRef, useState } from "react";
import { api, IS_MOCK, SSE_EVENT_KINDS, streamUrl } from "../api/client";
import type { RunDetail, RunEvent, RunSummary } from "../types";

export type StreamMode = "connecting" | "live" | "polling" | "closed";

const TERMINAL = new Set(["completed", "rejected", "aborted"]);
const REFRESH_KINDS = new Set([
  "node_end",
  "node_error",
  "gate_opened",
  "gate_answered",
  "run_paused",
  "run_failed",
  "run_finished",
  "run_cancelled",
  "provider_switched",
  "scope_violation",
  "agent_output",
]);

export function toSummary(d: RunDetail): RunSummary {
  const { id, requirement_id, title, provider, status, stage, outcome, error, created_at, updated_at, gate, stalled } = d;
  return { id, requirement_id, title, provider, status, stage, outcome, error, created_at, updated_at, gate, stalled };
}

/** Accumulated text of one LLM call, built from `llm_delta` events. */
export interface LiveCall {
  /** call id from the payload (stringified) */
  call: string;
  /** event.node of the deltas: the agent name (analyst, qa, evaluator, ...) */
  agent: string;
  provider: string;
  model: string;
  text: string;
  /** id of the first delta event: positions the stream within the event log */
  firstId: number;
  lastId: number;
}

export interface RunStream {
  /** All events except `llm_delta` (those are folded into `calls`). */
  events: RunEvent[];
  /** Live LLM output streams, ordered by first appearance. */
  calls: LiveCall[];
  summary: RunSummary | null;
  mode: StreamMode;
  /** Increments whenever something happened that changes the run detail (refetch trigger). */
  version: number;
}

/**
 * Live event feed for a run: loads history from /events, then follows /stream (SSE).
 * Reconnects with ?after=<last id>; after repeated SSE failures falls back to polling /events every 2 s.
 * `llm_delta` events are high volume: they are accumulated per call id and flushed at most every 50 ms,
 * and never added to `events`.
 */
export function useRunStream(runId: string | undefined, isTerminal = false): RunStream {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [calls, setCalls] = useState<LiveCall[]>([]);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [mode, setMode] = useState<StreamMode>("connecting");
  const [version, setVersion] = useState(0);
  const lastId = useRef(0);
  const terminalRef = useRef(isTerminal);
  terminalRef.current = isTerminal || (summary ? TERMINAL.has(summary.status) : false);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let retryTimer: number | undefined;
    let pollTimer: number | undefined;
    let frame: number | undefined;
    let failures = 0;
    let unsubscribeMock: (() => void) | undefined;
    const callMap = new Map<string, LiveCall>();
    lastId.current = 0;
    setEvents([]);
    setCalls([]);
    setSummary(null);
    setMode("connecting");

    const flushCalls = () => {
      frame = undefined;
      if (cancelled) return;
      setCalls([...callMap.values()].sort((a, b) => a.firstId - b.firstId));
    };
    const scheduleFlush = () => {
      if (frame !== undefined) return;
      frame = window.setTimeout(flushCalls, 50);
    };

    const addDelta = (e: RunEvent) => {
      const p = e.payload ?? {};
      const key = String(p.call ?? `${e.node ?? "agent"}-${e.id}`);
      const prev = callMap.get(key);
      const text = typeof p.text === "string" ? p.text : "";
      callMap.set(
        key,
        prev
          ? { ...prev, text: prev.text + text, lastId: e.id, model: String(p.model ?? prev.model), provider: String(p.provider ?? prev.provider) }
          : { call: key, agent: e.node ?? String(p.agent ?? ""), provider: String(p.provider ?? ""), model: String(p.model ?? ""), text, firstId: e.id, lastId: e.id },
      );
    };

    const addEvents = (list: RunEvent[]) => {
      if (cancelled || !list.length) return;
      const fresh = list.filter((e) => e.id > lastId.current).sort((a, b) => a.id - b.id);
      if (!fresh.length) return;
      lastId.current = fresh[fresh.length - 1].id;
      const regular: RunEvent[] = [];
      let sawDelta = false;
      for (const e of fresh) {
        if (e.kind === "llm_delta") {
          addDelta(e);
          sawDelta = true;
        } else {
          regular.push(e);
        }
      }
      if (sawDelta) scheduleFlush();
      if (!regular.length) return;
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        return [...prev, ...regular.filter((e) => !seen.has(e.id))];
      });
      if (regular.some((e) => REFRESH_KINDS.has(e.kind))) setVersion((v) => v + 1);
    };

    const catchUp = async () => {
      try {
        addEvents(await api.events(runId, lastId.current));
      } catch {
        /* surfaced through mode */
      }
    };

    const poll = async () => {
      if (cancelled) return;
      await catchUp();
      try {
        const detail = await api.run(runId);
        if (cancelled) return;
        const next = toSummary(detail);
        setSummary((prev) => {
          if (!prev || prev.status !== next.status || prev.stage !== next.stage) setVersion((v) => v + 1);
          return next;
        });
        if (TERMINAL.has(detail.status)) {
          setMode("closed");
          return;
        }
      } catch {
        /* keep polling */
      }
      pollTimer = window.setTimeout(poll, 2000);
    };

    const startPolling = () => {
      setMode("polling");
      void poll();
    };

    const onStatus = (s: RunSummary) => {
      if (cancelled) return;
      setSummary(s);
      setVersion((v) => v + 1);
    };

    const connect = () => {
      if (cancelled) return;
      if (typeof EventSource === "undefined") {
        startPolling();
        return;
      }
      const source = new EventSource(streamUrl(runId, lastId.current));
      es = source;
      source.onopen = () => {
        failures = 0;
        setMode("live");
      };
      const handler = (msg: MessageEvent) => {
        try {
          addEvents([JSON.parse(msg.data) as RunEvent]);
        } catch {
          /* ignore malformed frame */
        }
      };
      SSE_EVENT_KINDS.forEach((k) => source.addEventListener(k, handler as EventListener));
      source.onmessage = handler;
      source.addEventListener("status", ((msg: MessageEvent) => {
        try {
          onStatus(JSON.parse(msg.data) as RunSummary);
        } catch {
          /* ignore */
        }
        void catchUp(); // pick up event kinds we do not subscribe to explicitly
      }) as EventListener);
      source.onerror = () => {
        source.close();
        es = null;
        if (cancelled) return;
        if (terminalRef.current) {
          void catchUp();
          setMode("closed");
          return;
        }
        failures += 1;
        if (failures >= 3) {
          startPolling();
        } else {
          setMode("connecting");
          retryTimer = window.setTimeout(() => {
            void catchUp().then(connect);
          }, 1000 * failures);
        }
      };
    };

    if (IS_MOCK) {
      void import("../mocks/mockApi").then((m) => {
        if (cancelled) return;
        unsubscribeMock = m.subscribe(runId, 0, (e) => addEvents([e]), onStatus);
        setMode("live");
      });
    } else {
      void catchUp().then(connect);
    }

    return () => {
      cancelled = true;
      es?.close();
      window.clearTimeout(retryTimer);
      window.clearTimeout(pollTimer);
      if (frame !== undefined) {

        window.clearTimeout(frame);
      }
      unsubscribeMock?.();
    };
  }, [runId]);

  return { events, calls, summary, mode, version };
}
