"""LLM backends. Each one turns (schema, messages, tier) into a RawResult or raises ProviderError.

Backends do NOT validate against the schema; the router does that, so validation, repair and
failover behave identically for every provider.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field

from forgeline.config import ProviderModels, Settings
from forgeline.llm.errors import classify

Message = tuple[str, str]  # (role, content), role in {"system", "human", "ai"}


class TextResponse(BaseModel):
    """Pseudo-schema for the plain-text protocol.

    Code-heavy agents (QA, Developer) answer in plain text with fenced code / SEARCH-REPLACE blocks
    instead of JSON: small models under JSON-constrained decoding end the JSON string at the first
    unescaped double quote inside code, silently truncating it. Using a pseudo-schema keeps caching,
    retries, failover and usage tracking identical for both protocols.
    """

    text: str = Field(min_length=1)


@dataclass
class RawResult:
    model: str
    text: str  # raw text the model produced (used for repair prompts and the cache)
    parsed: dict[str, Any] | None = None  # provider-side parsed object, if any
    input_tokens: int = 0
    output_tokens: int = 0


def content_text(content: Any) -> str:
    """LangChain message content may be a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type", "text") == "text":
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return str(content)


class Backend:
    name: str = "base"

    def __init__(self, models: ProviderModels) -> None:
        self.models = models

    def model_for(self, tier: str) -> str:
        return self.models.for_tier(tier)

    def invoke(
        self, schema: type[BaseModel], messages: Sequence[Message], tier: str, temperature: float | None = None,
        on_token: TokenSink | None = None,
    ) -> RawResult:
        raise NotImplementedError


TokenSink = Callable[[str], None]


class _LangChainBackend(Backend):
    """Shared logic for LangChain chat models with native JSON-schema structured output."""

    def __init__(self, models: ProviderModels) -> None:
        super().__init__(models)
        self._clients: dict[tuple[str, float | None, str], Any] = {}

    def _make_client(self, model: str, temperature: float | None, json_schema: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError

    def _client(self, model: str, temperature: float | None, schema: type[BaseModel] | None = None) -> Any:
        key = (model, temperature, schema.__name__ if schema else "")
        if key not in self._clients:
            self._clients[key] = self._make_client(
                model, temperature, schema.model_json_schema() if schema else None
            )
        return self._clients[key]

    def _stream(
        self, schema: type[BaseModel], messages: Sequence[Message], model: str, temperature: float | None,
        on_token: TokenSink,
    ) -> RawResult:
        """Stream tokens to `on_token`. JSON agents stream through a schema-constrained client; the router
        parses and validates the accumulated text exactly like a non-streamed answer."""
        json_mode = schema is not TextResponse
        client = self._client(model, temperature, schema if json_mode else None)
        full: Any = None
        for chunk in client.stream(list(messages)):
            piece = content_text(getattr(chunk, "content", ""))
            if piece:
                on_token(piece)
            full = chunk if full is None else full + chunk
        text = content_text(getattr(full, "content", "")) if full is not None else ""
        usage = getattr(full, "usage_metadata", None) or {}
        return RawResult(
            model=model, text=text, parsed={"text": text} if not json_mode else None,
            input_tokens=int(usage.get("input_tokens", 0) or 0), output_tokens=int(usage.get("output_tokens", 0) or 0),
        )

    def invoke(
        self, schema: type[BaseModel], messages: Sequence[Message], tier: str, temperature: float | None = None,
        on_token: TokenSink | None = None,
    ) -> RawResult:
        model = self.model_for(tier)
        if on_token is not None:
            try:
                return self._stream(schema, messages, model, temperature, on_token)
            except Exception as exc:  # classify: provider failures propagate, others fall back below
                error = classify(self.name, exc)
                if error.status is not None or isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError)):
                    raise error from exc
                # streaming unsupported for this model/params: fall back to a normal call
        try:
            client = self._client(model, temperature)
            if schema is TextResponse:  # plain-text protocol: no JSON constraint at all
                raw = client.invoke(list(messages))
                text = content_text(getattr(raw, "content", ""))
                parsed: Any = {"text": text}
            else:
                out = client.with_structured_output(schema, method="json_schema", include_raw=True).invoke(
                    list(messages)
                )
                raw, parsed = out["raw"], out.get("parsed")
        except Exception as exc:  # every SDK failure is classified below
            raise classify(self.name, exc) from exc
        usage = getattr(raw, "usage_metadata", None) or {}
        return RawResult(
            model=model,
            text=content_text(getattr(raw, "content", "")),
            parsed=parsed.model_dump() if isinstance(parsed, BaseModel) else parsed,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )


class GeminiBackend(_LangChainBackend):
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.gemini)
        self.timeout = settings.request_timeout_s

    def _make_client(self, model: str, temperature: float | None, json_schema: dict[str, Any] | None = None) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI

        # max_retries=0: retries and failover are owned by our router, not the SDK.
        # Temperature is ignored: Gemini 3.x models use fixed sampling defaults.
        extra: dict[str, Any] = {}
        if json_schema is not None:  # streaming JSON agents: constrain the raw output to the schema
            extra = {"response_mime_type": "application/json", "response_schema": json_schema}
        return ChatGoogleGenerativeAI(model=model, max_retries=0, timeout=self.timeout, **extra)


class OllamaBackend(_LangChainBackend):
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.ollama)
        self.base_url = settings.ollama_base_url
        self.num_ctx = settings.ollama_num_ctx
        self.timeout = settings.ollama_timeout_s

    def _make_client(self, model: str, temperature: float | None, json_schema: dict[str, Any] | None = None) -> Any:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            base_url=self.base_url,
            temperature=0 if temperature is None else temperature,  # deterministic unless retrying
            num_ctx=self.num_ctx,  # default context (2-4k) is too small to hold source files
            keep_alive="15m",
            client_kwargs={"timeout": self.timeout},
            format=json_schema,  # schema-constrained decoding for streaming JSON agents
        )


# ---------------------------------------------------------------------------- mock (tests only)
Responder = dict[str, Any] | str | BaseException | Callable[[type[BaseModel], Sequence[Message]], Any]


@dataclass
class MockCall:
    schema: str
    tier: str
    messages: list[Message]
    temperature: float | None = None


@dataclass
class MockBackend(Backend):
    """Deterministic scripted backend for automated tests and fault injection.

    `responses` maps a schema class name to a list of responders consumed in order (the last one
    repeats). A responder is a dict (valid object), a str (raw text, possibly malformed), an
    exception instance (raised, as a provider failure), or a callable(schema, messages).
    """

    name: str = "mock"
    responses: dict[str, list[Responder]] = field(default_factory=dict)
    default: Responder | None = None
    calls: list[MockCall] = field(default_factory=list)
    models: ProviderModels = field(default_factory=lambda: ProviderModels("mock-light", "mock-code"))

    def invoke(
        self, schema: type[BaseModel], messages: Sequence[Message], tier: str, temperature: float | None = None,
        on_token: TokenSink | None = None,
    ) -> RawResult:
        result = self._respond(schema, messages, tier, temperature)
        if on_token is not None:  # simulate streaming in a few chunks
            step = max(1, len(result.text) // 3)
            for i in range(0, len(result.text), step):
                on_token(result.text[i : i + step])
        return result

    def _respond(
        self, schema: type[BaseModel], messages: Sequence[Message], tier: str, temperature: float | None
    ) -> RawResult:
        self.calls.append(MockCall(schema.__name__, tier, list(messages), temperature))
        queue = self.responses.get(schema.__name__)
        if queue:
            responder = queue.pop(0) if len(queue) > 1 else queue[0]
        elif self.default is not None:
            responder = self.default
        else:
            raise AssertionError(f"MockBackend {self.name!r} has no response for {schema.__name__}")
        if callable(responder) and not isinstance(responder, BaseException):
            responder = responder(schema, messages)
        if isinstance(responder, BaseException):
            raise classify(self.name, responder) from responder
        text = responder if isinstance(responder, str) else json.dumps(responder)
        words = sum(len(c.split()) for _, c in messages)
        return RawResult(
            model=self.model_for(tier),
            text=text,
            # JSON protocol: parsed=None forces the router's own JSON parsing + validation path
            parsed={"text": text} if schema is TextResponse else None,
            input_tokens=words,
            output_tokens=len(text.split()),
        )
