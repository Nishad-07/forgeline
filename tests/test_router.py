"""LLM router behaviour: retry, repair, failover, cache, provider switching and usage tracking."""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from forgeline.llm import AllProvidersUnavailable, LLMRouter, MockBackend, PriceTable, StructuredOutputError

GOOD = {"criteria": ["Given a captured payment, when refunding 40, then 60 remains"], "risk": "over-refund"}
MSGS = [("system", "You are an analyst."), ("human", "Partial refunds please.")]


class Answer(BaseModel):
    criteria: list[str]
    risk: str


class Http503(Exception):
    status_code = 503


class Http401(Exception):
    status_code = 401


def make_router(db=None, *, primary="gemini", failover=True, cache=False, **responses):
    gemini = MockBackend(name="gemini", responses={"Answer": list(responses.get("gemini", [GOOD]))})
    ollama = MockBackend(name="ollama", responses={"Answer": list(responses.get("ollama", [GOOD]))})
    events: list[tuple[str, dict]] = []
    router = LLMRouter(
        {"gemini": gemini, "ollama": ollama},
        primary,
        failover=failover,
        db=db,
        cache_enabled=cache,
        prices=PriceTable({}, {"gemini": {"input": 1.0, "output": 2.0}}),
        on_event=lambda run_id, kind, agent, payload: events.append((kind, payload)),
        sleep=lambda s: None,
        stream=False,  # these tests assert exact event sequences; streaming is covered elsewhere
    )
    return router, gemini, ollama, events


def test_happy_path_records_usage(db):
    router, gemini, ollama, events = make_router(db)
    result = router.structured(Answer, MSGS, agent="analyst", run_id="r1")
    assert result.risk == "over-refund"
    assert len(gemini.calls) == 1 and not ollama.calls
    calls = db.llm_calls("r1")
    assert len(calls) == 1
    assert calls[0]["status"] == "ok" and calls[0]["provider"] == "gemini" and calls[0]["cost_usd"] > 0
    assert [k for k, _ in events] == ["llm_call"]


def test_malformed_output_is_repaired(db):
    router, gemini, _, events = make_router(db, gemini=["Sure! here you go: {not json", {"criteria": "x"}, GOOD])
    result = router.structured(Answer, MSGS, agent="analyst", run_id="r1")
    assert result == Answer(**GOOD)
    assert len(gemini.calls) == 3
    # the repair prompt contains the validation error and the bad answer
    last_messages = gemini.calls[-1].messages
    assert last_messages[-2][0] == "ai" and "did not match" in last_messages[-1][1]
    assert [c["status"] for c in db.llm_calls("r1")] == ["invalid_output", "invalid_output", "repaired"]
    assert [k for k, _ in events].count("llm_repair") == 2


def test_fenced_json_is_accepted():
    router, *_ = make_router(gemini=['```json\n{"criteria": ["a"], "risk": "b"}\n```'])
    assert router.structured(Answer, MSGS).risk == "b"


def test_repairs_exhausted_fails_over_to_other_provider(db):
    router, gemini, ollama, events = make_router(db, gemini=["garbage"])
    result = router.structured(Answer, MSGS, run_id="r1")
    assert result == Answer(**GOOD)
    assert len(gemini.calls) == 3  # 1 + 2 repairs
    assert len(ollama.calls) == 1
    assert ("llm_failover", {"from": "gemini", "to": "ollama"}) == (events[-2][0], {
        k: events[-2][1][k] for k in ("from", "to")})


def test_malformed_everywhere_raises_structured_output_error():
    router, *_ = make_router(gemini=["garbage"], ollama=["still garbage"])
    with pytest.raises(StructuredOutputError):
        router.structured(Answer, MSGS)


def test_transient_error_is_retried_then_succeeds(db):
    router, gemini, ollama, events = make_router(db, gemini=[Http503("503 high demand"), GOOD])
    assert router.structured(Answer, MSGS, run_id="r1") == Answer(**GOOD)
    assert len(gemini.calls) == 2 and not ollama.calls
    assert [c["status"] for c in db.llm_calls("r1")] == ["provider_error", "ok"]
    assert "llm_retry" in [k for k, _ in events]


def test_provider_down_mid_workflow_fails_over(db):
    down = httpx.ConnectError("connection refused")
    router, gemini, ollama, events = make_router(db, gemini=[down])
    assert router.structured(Answer, MSGS, run_id="r1") == Answer(**GOOD)
    assert len(gemini.calls) == 2  # first try + one transient retry
    assert len(ollama.calls) == 1
    assert "llm_failover" in [k for k, _ in events]


def test_auth_error_fails_over_without_retry():
    router, gemini, ollama, _ = make_router(gemini=[Http401("401 API key invalid")])
    router.structured(Answer, MSGS)
    assert len(gemini.calls) == 1 and len(ollama.calls) == 1


def test_all_providers_down_raises_resumable_error():
    router, *_ = make_router(gemini=[Http503("503")], ollama=[httpx.ConnectError("refused")])
    with pytest.raises(AllProvidersUnavailable) as exc:
        router.structured(Answer, MSGS)
    assert len(exc.value.errors) == 2


def test_failover_disabled_stays_on_primary():
    router, _, ollama, _ = make_router(failover=False, gemini=[Http503("503")])
    with pytest.raises(AllProvidersUnavailable):
        router.structured(Answer, MSGS)
    assert not ollama.calls


def test_switch_primary_provider():
    router, gemini, ollama, _ = make_router(primary="ollama")
    router.structured(Answer, MSGS)
    assert len(ollama.calls) == 1 and not gemini.calls
    router.set_primary("gemini")
    router.structured(Answer, MSGS)
    assert len(gemini.calls) == 1
    assert router.provider_order() == ["gemini", "ollama"]


def test_cache_hit_skips_provider(db):
    router, gemini, _, _events = make_router(db, cache=True)
    first = router.structured(Answer, MSGS, run_id="r1")
    second = router.structured(Answer, MSGS, run_id="r1")
    assert first == second
    assert len(gemini.calls) == 1
    statuses = [c["status"] for c in db.llm_calls("r1")]
    assert statuses == ["ok", "cached"]
    assert db.llm_calls("r1")[1]["cost_usd"] == 0
    # different prompt -> cache miss
    router.structured(Answer, [*MSGS, ("human", "more detail")], run_id="r1")
    assert len(gemini.calls) == 2


def test_rate_limit_spaces_calls():
    slept: list[float] = []
    backend = MockBackend(name="gemini", responses={"Answer": [GOOD]})
    router = LLMRouter({"gemini": backend}, "gemini", rpm={"gemini": 60}, sleep=slept.append)
    router.structured(Answer, MSGS)
    router.structured(Answer, MSGS)
    assert slept and 0 < slept[0] <= 1.0
