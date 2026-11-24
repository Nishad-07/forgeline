"""HTTP API tests (docs/API.md): FastAPI TestClient over an Orchestrator driven by the scripted mock LLM.

No network: both providers are MockBackends and the Ollama availability probe is monkeypatched.
"""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_workflow import ACCEPTANCE_101, GOOD_101, ScriptedLLM, analysis, design

from forgeline.api import server
from forgeline.api.server import create_app
from forgeline.config import Settings
from forgeline.graph.build import NODE_ORDER
from forgeline.llm import LLMRouter, MockBackend
from forgeline.orchestrator import Orchestrator

REQ = "requirements/partial_refund.md"


def build_orchestrator(base, script: ScriptedLLM) -> Orchestrator:
    settings = Settings.from_env().with_overrides(
        data_dir=base / "data", workspace_dir=base / "workspace", cache_enabled=False, dev_output_mode="edits",
    )
    backends = {"ollama": MockBackend(name="ollama", default=script),
                "gemini": MockBackend(name="gemini", default=script)}

    def router_factory(provider, cache, db):
        return LLMRouter(backends, provider, db=db, cache_enabled=False, sleep=lambda s: None)

    return Orchestrator(settings, router_factory=router_factory)


@pytest.fixture(scope="module")
def light(tmp_path_factory):
    """Client for endpoints that do not execute a workflow."""
    base = tmp_path_factory.mktemp("api_light")
    orch = build_orchestrator(base, ScriptedLLM(analyst=[analysis()]))
    with TestClient(create_app(orch, dashboard_dir=base / "no_dashboard")) as client:
        yield client


def wait_for(client: TestClient, run_id: str, predicate, timeout: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        if predicate(run):
            return run
        assert run["status"] not in {"failed", "paused_provider_unavailable"}, run
        assert time.monotonic() < deadline, f"timed out waiting; last summary: {run}"
        time.sleep(0.3)


def gate_is(kind: str):
    return lambda run: run["status"] == "awaiting_human" and (run["gate"] or {}).get("gate") == kind


# ---------------------------------------------------------------------------- static endpoints
def test_health(light):
    assert light.get("/api/health").json() == {"status": "ok", "version": "0.1.0"}


def test_requirements_list(light):
    reqs = light.get("/api/requirements").json()
    by_path = {r["path"]: r for r in reqs}
    assert REQ in by_path
    assert by_path[REQ]["id"] == "REQ-101"
    assert set(by_path[REQ]) == {"path", "id", "title", "priority", "body"}
    assert by_path[REQ]["body"]


def test_policy(light):
    body = light.get("/api/policy").json()
    assert len(body["sha256"]) == 64
    assert isinstance(body["policy"], dict) and body["policy"]


def test_graph_shape(light):
    graph = light.get("/api/graph").json()
    assert [n["id"] for n in graph["nodes"]] == NODE_ORDER
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}
    assert kinds["apply_change"] == "guard" and kinds["approval"] == "human" and kinds["intake"] == "system"
    assert kinds["developer"] == "agent" and kinds["escalate"] == "human" and kinds["clarify"] == "human"
    assert all(n["label"] for n in graph["nodes"])
    ids = set(NODE_ORDER)
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    labels = {e.get("label") for e in graph["edges"]}
    for label in ("objection", "rejected", "failing", "blocking", "approve", "request changes", "retry", "override"):
        assert label in labels
    pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("release", "approval") in pairs and ("approval", "merge_deploy") in pairs
    assert ("escalate", "evaluate") in pairs and ("clarify", "analyst") in pairs


def test_providers_ollama_up(light, monkeypatch):
    async def tags(base_url):
        return ["qwen2.5-coder:3b", "llama3:latest"]

    monkeypatch.setattr(server, "_ollama_tags", tags)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    body = light.get("/api/providers").json()
    assert body["default"] in {"gemini", "ollama"} and isinstance(body["failover"], bool)
    providers = {p["name"]: p for p in body["providers"]}
    assert providers["gemini"]["available"] is True and providers["gemini"]["detail"] == "key present"
    assert set(providers["gemini"]["models"]) == {"light", "code"}
    ollama = providers["ollama"]
    assert ollama["available"] is True and "reachable" in ollama["detail"]
    assert set(ollama["models"]) == {"light", "code"}


def test_providers_ollama_down(light, monkeypatch):
    async def tags(base_url):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(server, "_ollama_tags", tags)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    providers = {p["name"]: p for p in light.get("/api/providers").json()["providers"]}
    assert providers["gemini"]["available"] is False
    assert providers["ollama"]["available"] is False and "not reachable" in providers["ollama"]["detail"]


def test_unknown_run_is_404(light):
    for path in ("", "/events", "/stream?once=1", "/diff", "/usage", "/audit", "/approvals"):
        resp = light.get(f"/api/runs/nope{path}")
        assert resp.status_code == 404, path
        assert "detail" in resp.json()
    assert light.post("/api/runs/nope/resume", json={"decision": None}).status_code == 404


def test_start_run_rejects_bad_requirement_path(light):
    assert light.post("/api/runs", json={"requirement_path": "../outside.md"}).status_code == 400
    assert light.post("/api/runs", json={"requirement_path": "requirements/missing.md"}).status_code == 404
    assert light.post("/api/runs", json={"requirement_path": REQ, "provider": "openai"}).status_code == 422


def test_cors_allows_vite_dev_server(light):
    resp = light.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_dashboard_served_with_spa_fallback(tmp_path, light):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>Forgeline dashboard</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    orch = light.app.state.runtime.orch
    with TestClient(create_app(orch, dashboard_dir=dist)) as client:
        assert "Forgeline dashboard" in client.get("/").text
        assert "Forgeline dashboard" in client.get("/runs/some-run").text  # client-side route
        assert "console.log" in client.get("/assets/app.js").text
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/health").json()["status"] == "ok"  # API takes precedence
        missing = client.get("/api/does-not-exist")
        assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}


# ---------------------------------------------------------------------------- full lifecycle
def test_full_run_lifecycle_over_http(tmp_path):
    script = ScriptedLLM(analyst=[analysis(["Notify on every partial refund?"]), analysis()],
                         architect=[design()], qa=[ACCEPTANCE_101], developer=[GOOD_101])
    orch = build_orchestrator(tmp_path, script)
    with TestClient(create_app(orch, dashboard_dir=tmp_path / "no_dashboard")) as client:
        resp = client.post("/api/runs", json={"requirement_path": REQ, "provider": "ollama", "cache": False})
        assert resp.status_code == 202
        summary = resp.json()
        run_id = summary["id"]
        assert summary["requirement_id"] == "REQ-101" and summary["provider"] == "ollama"
        assert summary["status"] in {"created", "running", "awaiting_human"}
        assert summary["created_at"] and summary["updated_at"]

        # clarification gate
        run = wait_for(client, run_id, gate_is("clarification"))
        assert run["gate"]["questions"] == ["Notify on every partial refund?"]
        assert run["requirement"]["id"] == "REQ-101" and run["requirement"]["body"]
        assert run["state"]["analysis"]["clarifying_questions"] and run["next"] == ["clarify"]
        assert "diff" not in run and "diff" not in run["state"]
        assert [r["id"] for r in client.get("/api/runs").json()] == [run_id]
        assert client.get(f"/api/runs/{run_id}/diff").json()["stat"]["files"] == 0

        # events with after=
        events = client.get(f"/api/runs/{run_id}/events").json()
        kinds = [e["kind"] for e in events]
        assert kinds[0] == "run_created" and "gate_opened" in kinds and "node_end" in kinds
        ids = [e["id"] for e in events]
        assert ids == sorted(ids)
        mid = ids[len(ids) // 2]
        later = client.get(f"/api/runs/{run_id}/events", params={"after": mid}).json()
        assert later and all(e["id"] > mid for e in later) and len(later) == len([i for i in ids if i > mid])
        assert set(events[0]) >= {"id", "run_id", "ts", "node", "kind", "payload"}

        # SSE backlog + status, then end (?once=1)
        stream = client.get(f"/api/runs/{run_id}/stream", params={"once": 1})
        assert stream.status_code == 200 and stream.headers["content-type"].startswith("text/event-stream")
        text = stream.text
        assert "event: run_created" in text and "event: gate_opened" in text and "event: status" in text
        assert f"id: {ids[0]}" in text and "data: {" in text
        resumed = client.get(f"/api/runs/{run_id}/stream", params={"once": 1, "after": ids[-1]}).text
        assert "event: run_created" not in resumed and "event: status" in resumed

        # a decision that does not fit the gate is rejected synchronously
        resp = client.post(f"/api/runs/{run_id}/resume", json={"decision": {"decision": "approve"}})
        assert resp.status_code == 409 and "answers" in resp.json()["detail"]

        resp = client.post(f"/api/runs/{run_id}/resume",
                           json={"decision": {"answers": ["Yes, every refund."], "actor": "pm"}})
        assert resp.status_code == 202 and resp.json()["id"] == run_id

        # release approval gate
        run = wait_for(client, run_id, gate_is("release_approval"))
        assert run["gate"]["recommendation"] == "approve" and run["next"] == ["approval"]
        assert run["state"]["clarifications"][0]["answer"] == "Yes, every refund."
        diff = client.get(f"/api/runs/{run_id}/diff").json()
        assert "PARTIALLY_REFUNDED" in diff["diff"]
        assert diff["stat"]["files"] >= 2 and diff["stat"]["added"] > 0

        resp = client.post(f"/api/runs/{run_id}/resume", json={"decision": {"decision": "ship it"}})
        assert resp.status_code == 409
        resp = client.post(f"/api/runs/{run_id}/resume",
                           json={"decision": {"decision": "approve", "actor": "asha", "comment": "LGTM"}})
        assert resp.status_code == 202

        run = wait_for(client, run_id, lambda r: r["status"] in {"completed", "rejected", "aborted"})
        assert run["status"] == "completed" and run["outcome"] == "merged" and run["gate"] is None
        assert run["state"]["outcome"] == "merged"
        assert client.post(f"/api/runs/{run_id}/resume", json={"decision": None}).status_code == 409

        approvals = client.get(f"/api/runs/{run_id}/approvals").json()
        assert [(a["gate"], a["decision"], a["actor"]) for a in approvals] == [
            ("clarification", "answered", "pm"), ("release_approval", "approve", "asha")]
        usage = client.get(f"/api/runs/{run_id}/usage").json()
        assert usage["totals"]["calls"] >= 6 and usage["by_agent"] and len(usage["calls"]) == usage["totals"]["calls"]
        audit = client.get(f"/api/runs/{run_id}/audit").json()
        assert audit["verification"]["ok"] is True
        assert audit["verification"]["entries"] == len(audit["entries"]) > 0
        assert "merged_to_main" in [e["action"] for e in audit["entries"]]
        assert "PARTIALLY_REFUNDED" in client.get(f"/api/runs/{run_id}/diff").json()["diff"]
        final_kinds = [e["kind"] for e in client.get(f"/api/runs/{run_id}/events").json()]
        assert "gate_answered" in final_kinds and final_kinds[-1] == "run_finished"


# ---------------------------------------------------------------------------- stopping runs
def test_cancel_run_and_stalled_flag(tmp_path):
    orch = build_orchestrator(tmp_path, ScriptedLLM(analyst=[analysis(["Notify on every refund?"])]))
    with TestClient(create_app(orch, dashboard_dir=tmp_path / "none")) as client:
        started = client.post("/api/runs", json={"requirement_path": REQ, "provider": "ollama"})
        assert started.status_code == 202
        run_id = started.json()["id"]
        wait_for(client, run_id, gate_is("clarification"))

        # A run abandoned by a crashed process: marked running, nothing executes it, silent for a while.
        orch.db.create_run("ghost-run", requirement_id="REQ-X", title="Abandoned", provider="gemini",
                           status="running")
        orch.db.execute("UPDATE runs SET updated_at = '2020-01-01T00:00:00.000+00:00' WHERE id = 'ghost-run'")
        runs = {r["id"]: r for r in client.get("/api/runs").json()}
        assert runs["ghost-run"]["stalled"] is True and runs[run_id]["stalled"] is False

        for target in (run_id, "ghost-run"):
            stopped = client.post(f"/api/runs/{target}/cancel", json={"actor": "asha"})
            assert stopped.status_code == 200
            assert stopped.json()["status"] == "aborted" and stopped.json()["outcome"] == "cancelled"
            assert client.post(f"/api/runs/{target}/cancel").status_code == 409
        assert client.post("/api/runs/unknown/cancel").status_code == 404
        assert client.get(f"/api/runs/{run_id}/audit").json()["verification"]["ok"] is True
