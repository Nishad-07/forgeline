"""LLM router: one entry point for every agent's model call.

Responsibilities, in order of escalation:
1. Cache       - identical (provider, model, schema, messages) requests are answered from SQLite.
2. Rate limit  - per-provider minimum spacing between requests (Gemini free tier is tight).
3. Retry       - transient provider errors (429, 5xx, timeouts) are retried with backoff.
4. Repair      - output that fails schema validation is sent back with the validation error.
5. Failover    - if a provider is unavailable or keeps returning bad output, try the next one.
6. Give up     - AllProvidersUnavailable (workflow pauses, resumable) or StructuredOutputError.

Every attempt is recorded in `llm_calls` (tokens, latency, equivalent cost, status) and notable
moments (retry, repair, failover) are emitted as workflow events for the dashboard.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from forgeline.config import PROVIDERS, Settings
from forgeline.llm.backends import Backend, GeminiBackend, Message, OllamaBackend, RawResult, TextResponse
from forgeline.llm.errors import AllProvidersUnavailable, ProviderError, StructuredOutputError
from forgeline.llm.usage import PriceTable
from forgeline.store.audit import canonical
from forgeline.store.db import Database

T = TypeVar("T", bound=BaseModel)
EventSink = Callable[[str | None, str, str, dict[str, Any]], None]  # (run_id, kind, agent, payload)

REPAIR_PROMPT = (
    "Your previous answer could not be accepted because it did not match the required JSON schema.\n"
    "Validation error:\n{error}\n\n"
    "Reply again with ONLY a JSON object that matches the schema exactly. No prose, no markdown fences."
)


def parse_json_text(text: str) -> Any:
    """Parse JSON from model text, tolerating ``` fences and leading/trailing prose."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def validate(schema: type[T], raw: RawResult) -> T:
    payload = raw.parsed if raw.parsed is not None else parse_json_text(raw.text)
    return schema.model_validate(payload)


class LLMRouter:
    def __init__(
        self,
        backends: dict[str, Backend],
        primary: str,
        *,
        failover: bool = True,
        max_repairs: int = 2,
        transient_retries: int = 1,
        backoff_s: float = 2.0,
        rpm: dict[str, int] | None = None,
        db: Database | None = None,
        prices: PriceTable | None = None,
        cache_enabled: bool = True,
        on_event: EventSink | None = None,
        sleep: Callable[[float], None] = time.sleep,
        stream: bool = True,
    ) -> None:
        if primary not in backends:
            raise ValueError(f"primary provider {primary!r} not in {list(backends)}")
        self.backends = backends
        self.primary = primary
        self.failover = failover
        self.max_repairs = max_repairs
        self.transient_retries = transient_retries
        self.backoff_s = backoff_s
        self.rpm = rpm or {}
        self.db = db
        self.prices = prices or PriceTable({}, {})
        self.cache_enabled = cache_enabled
        self.on_event = on_event
        self._sleep = sleep
        self._last_call: dict[str, float] = {}
        self._throttle_lock = threading.Lock()
        self.stream = stream  # emit live `llm_delta` events while a model generates
        self._call_seq = itertools.count(1)

    # ------------------------------------------------------------------ public API
    def set_primary(self, provider: str) -> None:
        if provider not in self.backends:
            raise ValueError(f"unknown provider {provider!r}")
        self.primary = provider

    def text(
        self,
        messages: Sequence[Message],
        *,
        tier: str = "light",
        agent: str = "unknown",
        run_id: str | None = None,
        temperature: float | None = None,
        order: list[str] | None = None,
    ) -> str:
        """Plain-text completion with the same cache/retry/failover/usage behaviour as `structured`."""
        return self.structured(
            TextResponse, messages, tier=tier, agent=agent, run_id=run_id, temperature=temperature, order=order
        ).text

    def provider_order(self) -> list[str]:
        others = [p for p in self.backends if p != self.primary]
        return [self.primary, *others] if self.failover else [self.primary]

    def structured(
        self,
        schema: type[T],
        messages: Sequence[Message],
        *,
        tier: str = "light",
        agent: str = "unknown",
        run_id: str | None = None,
        temperature: float | None = None,
        order: list[str] | None = None,
    ) -> T:
        """Return a validated `schema` instance, or raise AllProvidersUnavailable / StructuredOutputError.

        `temperature` overrides the backend default (used to vary output on repair retries).
        `order` overrides the provider order for this call (used for agent-level escalation).
        """
        errors: list[Exception] = []
        order = order or self.provider_order()
        for index, provider in enumerate(order):
            try:
                return self._call_provider(provider, schema, messages, tier=tier, agent=agent, run_id=run_id,
                                           temperature=temperature)
            except (ProviderError, StructuredOutputError) as exc:
                errors.append(exc)
                nxt = order[index + 1] if index + 1 < len(order) else None
                self.emit(
                    run_id,
                    "llm_failover" if nxt else "llm_provider_failed",
                    agent,
                    {"from": provider, "to": nxt, "error": str(exc)[:300]},
                )
        if errors and all(isinstance(e, StructuredOutputError) for e in errors):
            raise errors[-1]
        raise AllProvidersUnavailable(errors)

    # ------------------------------------------------------------------ internals
    def _call_provider(
        self,
        provider: str,
        schema: type[T],
        messages: Sequence[Message],
        *,
        tier: str,
        agent: str,
        run_id: str | None,
        temperature: float | None = None,
    ) -> T:
        backend = self.backends[provider]
        model = backend.model_for(tier)
        key = self._cache_key(provider, model, schema, messages, temperature)

        cached = self._cache_lookup(key, schema)
        if cached is not None:
            obj, row = cached
            self._record(run_id, agent, provider, model, tier, row["input_tokens"], row["output_tokens"],
                         0.0, cached=True, attempt=1, status="cached")
            self.emit(run_id, "llm_call", agent, {"provider": provider, "model": model, "cached": True})
            return obj

        convo = list(messages)
        repairs = 0
        transient_failures = 0
        attempt = 0
        while True:
            attempt += 1
            self._throttle(provider)
            started = time.perf_counter()
            batcher = None
            if self.stream and (run_id or self.on_event):
                batcher = DeltaBatcher(self, run_id, agent, f"{agent}-{next(self._call_seq)}", provider, model)
            try:
                raw = backend.invoke(schema, convo, tier, temperature=temperature,
                                     on_token=batcher.push if batcher else None)
            except ProviderError as exc:
                latency = (time.perf_counter() - started) * 1000
                self._record(run_id, agent, provider, model, tier, 0, 0, latency,
                             attempt=attempt, status="provider_error", error=str(exc)[:500])
                if exc.transient and transient_failures < self.transient_retries:
                    transient_failures += 1
                    delay = self.backoff_s * (2 ** (transient_failures - 1))
                    self.emit(run_id, "llm_retry", agent,
                               {"provider": provider, "delay_s": delay, "error": str(exc)[:200]})
                    self._sleep(delay)
                    continue
                raise
            finally:
                if batcher:
                    batcher.flush()
            latency = (time.perf_counter() - started) * 1000

            try:
                obj = validate(schema, raw)
            except (ValidationError, ValueError) as exc:
                self._record(run_id, agent, provider, model, tier, raw.input_tokens, raw.output_tokens,
                             latency, attempt=attempt, status="invalid_output", error=str(exc)[:500])
                if repairs >= self.max_repairs:
                    raise StructuredOutputError(provider, schema.__name__, attempt, str(exc)[:500]) from exc
                repairs += 1
                self.emit(run_id, "llm_repair", agent,
                           {"provider": provider, "repair": repairs, "error": str(exc)[:300]})
                convo = [*convo, ("ai", raw.text[:6000] or "(empty)"), ("human", REPAIR_PROMPT.format(error=exc))]
                continue

            status = "repaired" if repairs else "ok"
            self._record(run_id, agent, provider, model, tier, raw.input_tokens, raw.output_tokens,
                         latency, attempt=attempt, status=status)
            self.emit(run_id, "llm_call", agent, {
                "provider": provider, "model": model, "latency_ms": round(latency),
                "input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "repairs": repairs,
            })
            self._cache_store(key, provider, model, obj, raw)
            return obj

    def _throttle(self, provider: str) -> None:
        rpm = self.rpm.get(provider, 0)
        if rpm <= 0:
            return
        interval = 60.0 / rpm
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._last_call.get(provider, 0.0) + interval - now
            if wait > 0:
                self._sleep(wait)
            self._last_call[provider] = time.monotonic()

    @staticmethod
    def _cache_key(
        provider: str, model: str, schema: type[BaseModel], messages: Sequence[Message],
        temperature: float | None = None,
    ) -> str:
        material = canonical({
            "provider": provider, "model": model, "schema": schema.__name__, "temperature": temperature,
            "json_schema": schema.model_json_schema(), "messages": [list(m) for m in messages],
        })
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _cache_lookup(self, key: str, schema: type[T]) -> tuple[T, dict[str, Any]] | None:
        if not (self.cache_enabled and self.db):
            return None
        row = self.db.cache_get(key)
        if row is None:
            return None
        try:
            return schema.model_validate_json(row["response"]), row
        except ValidationError:
            return None  # schema changed since this entry was cached

    def _cache_store(self, key: str, provider: str, model: str, obj: BaseModel, raw: RawResult) -> None:
        if self.cache_enabled and self.db:
            self.db.cache_put(key, provider, model, obj.model_dump_json(), raw.input_tokens, raw.output_tokens)

    def _record(
        self, run_id: str | None, agent: str, provider: str, model: str, tier: str,
        input_tokens: int, output_tokens: int, latency_ms: float, *,
        cached: bool = False, attempt: int = 1, status: str = "ok", error: str | None = None,
    ) -> None:
        if self.db is None:
            return
        cost = 0.0 if cached else self.prices.cost(provider, model, input_tokens, output_tokens)
        self.db.record_llm_call(
            run_id=run_id, agent=agent, provider=provider, model=model, tier=tier,
            input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=round(latency_ms, 1),
            cost_usd=cost, cached=int(cached), attempt=attempt, status=status, error=error,
        )

    def emit(self, run_id: str | None, kind: str, agent: str, payload: dict[str, Any]) -> None:
        if self.db is not None and run_id:
            self.db.add_event(run_id, kind, node=agent, **payload)
        if self.on_event is not None:
            self.on_event(run_id, kind, agent, payload)


class DeltaBatcher:
    """Collects streamed tokens and emits them as `llm_delta` events in small batches (~4/s)."""

    def __init__(self, router: LLMRouter, run_id: str | None, agent: str, call: str, provider: str,
                 model: str, interval_s: float = 0.25, max_chars: int = 400) -> None:
        self.router, self.run_id, self.agent = router, run_id, agent
        self.meta = {"call": call, "provider": provider, "model": model}
        self.interval_s, self.max_chars = interval_s, max_chars
        self._buffer: list[str] = []
        self._size = 0
        self._last = time.monotonic()

    def push(self, text: str) -> None:
        self._buffer.append(text)
        self._size += len(text)
        if self._size >= self.max_chars or time.monotonic() - self._last >= self.interval_s:
            self.flush()

    def flush(self) -> None:
        if self._buffer:
            self.router.emit(self.run_id, "llm_delta", self.agent, {**self.meta, "text": "".join(self._buffer)})
            self._buffer, self._size = [], 0
        self._last = time.monotonic()


def build_router(
    settings: Settings,
    db: Database | None = None,
    *,
    primary: str | None = None,
    failover: bool | None = None,
    cache_enabled: bool | None = None,
    max_repairs: int = 2,
    on_event: EventSink | None = None,
) -> LLMRouter:
    """Create a router over the real Gemini and Ollama backends."""
    backends: dict[str, Backend] = {"gemini": GeminiBackend(settings), "ollama": OllamaBackend(settings)}
    chosen = (primary or settings.default_provider).lower()
    if chosen not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    return LLMRouter(
        backends,
        chosen,
        failover=settings.failover if failover is None else failover,
        max_repairs=max_repairs,
        rpm={"gemini": settings.gemini_rpm, "ollama": settings.ollama_rpm},
        db=db,
        prices=PriceTable.load(settings.pricing_path),
        cache_enabled=settings.cache_enabled if cache_enabled is None else cache_enabled,
        on_event=on_event,
    )
