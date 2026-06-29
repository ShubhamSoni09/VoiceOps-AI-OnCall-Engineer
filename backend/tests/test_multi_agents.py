from __future__ import annotations

import subprocess
import textwrap
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.models import AgentRun, AgentRunStatus
from app.agents.service import MultiAgentService, get_multi_agent_service
from app.agents.store import AgentRunStore
from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
import app.voice_agent.router as voice_router


@pytest.fixture
def client(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text(
        textwrap.dedent(
            """
            from fastapi import FastAPI

            app = FastAPI()

            # GET /health is intentionally missing
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (workspace / "test_app.py").write_text(
        textwrap.dedent(
            """
            from app import app


            def test_health_route_exists():
                routes = [route.path for route in app.routes]
                assert "/health" in routes
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "initial", "-q"], cwd=workspace, check=True)

    users_path = tmp_path / "users.json"
    settings = Settings(
        users_store_path=users_path,
        jwt_secret="multi-agent-test-secret",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        rag_index_path=tmp_path / "rag-index.json",
        voiceops_cache_path=tmp_path / "cache.json",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(users_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    agents = MultiAgentService(AgentRunStore(tmp_path / "agent-runs.json"), collab, settings)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents

    with TestClient(app) as test_client:
        test_client.agent_service = agents
        test_client.agent_runs_path = tmp_path / "agent-runs.json"
        yield test_client, workspace

    voice_router._pipeline = None
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


def _token(client: TestClient) -> str:
    response = client.post("/auth/login", json={"email": "priya@voiceops.dev", "password": "oncall123"})
    assert response.status_code == 200
    return response.json()["access_token"]


def _login_token(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_multi_agent_patch_run_creates_audited_pending_action(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    joined = test_client.post(
        "/collab/rooms/main/join",
        headers=headers,
        json={"room_name": "Team room", "project": "workspace"},
    )
    assert joined.status_code == 200

    seed = test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "Open task: fix the missing health check in app.py", "source": "meeting_audio"},
    )
    assert seed.status_code == 200

    run = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "AI fix that", "source": "meeting"},
    )

    assert run.status_code == 200
    data = run.json()
    assert data["status"] == "completed"
    assert data["route"] == "meeting_patch_closure"
    assert data["action_id"]
    assert data["metadata"]["plan"]["route"] == "meeting_patch_closure"
    assert data["metadata"]["plan"]["metadata"]["approval_required"] is True
    assert data["steps"][0]["metadata"]["plan"]["steps"][0]["role"] == "meeting"
    assert [step["role"] for step in data["steps"]] == [
        "coordinator",
        "meeting",
        "memory",
        "code",
        "review",
        "test",
        "git",
    ]
    assert [(item["from_role"], item["to_role"]) for item in data["exchanges"]] == [
        ("meeting", "memory"),
        ("memory", "code"),
        ("code", "review"),
        ("review", "test"),
        ("test", "git"),
    ]
    assert (workspace / "app.py").read_text(encoding="utf-8").find('@app.get("/health")') == -1

    snapshot = test_client.get("/collab/rooms/main", headers=headers)
    assert snapshot.status_code == 200
    room = snapshot.json()
    action = next(item for item in room["actions"] if item["id"] == data["action_id"])
    assert action["status"] == "pending_approval"
    assert action["pending_approval"] is True
    assert action["approval"]["diff"]
    assert action["approval"]["preapproval_test"]["passed"] is True
    assert action["approval"]["preapproval_test"]["workspace"] == "temporary"
    assert action["approval"]["policy"]["mode"] == "preview_first"
    assert action["approval"]["policy"]["workspace_write_before_approval"] is False
    assert action["approval"]["multi_agent"]["run_id"] == data["id"]
    assert action["approval"]["multi_agent"]["route"] == "meeting_patch_closure"
    assert action["approval"]["multi_agent"]["plan"]["metadata"]["approval_required"] is True
    assert action["approval"]["multi_agent"]["llm_routes"]["code"]["provider"] == "mock"
    assert action["approval"]["llm"]["provider"] == "mock"
    assert action["approval"]["llm"]["fallback_strategy"] == "heuristic_health_fix"
    agent_messages = [
        message
        for message in room["messages"]
        if message["metadata"].get("source") == "multi_agent_run"
    ]
    assert agent_messages
    assert agent_messages[-1]["metadata"]["roles"] == [
        "coordinator",
        "meeting",
        "memory",
        "code",
        "review",
        "test",
        "git",
    ]
    assert agent_messages[-1]["metadata"]["exchange_count"] == 5


def test_agent_assignment_queue_records_timeline_and_status(client):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    created = test_client.post(
        "/agents/rooms/main/assignments",
        headers=headers,
        json={
            "agent_id": "codex",
            "agent_label": "OpenAI Codex",
            "agent_kind": "external",
            "task": "Review the pending health check patch before approval",
            "mode": "review",
            "model": "gpt-5.4",
        },
    )
    assert created.status_code == 200
    assignment = created.json()
    assert assignment["status"] == "queued"
    assert assignment["agent_kind"] == "external"
    assert assignment["requested_by_name"] == "Priya Nair"
    assert assignment["metadata"]["requested_by_name"] == "Priya Nair"
    assert assignment["metadata"]["model"] == "gpt-5.4"

    listed = test_client.get("/agents/rooms/main/assignments", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["assignments"][0]["id"] == assignment["id"]

    updated = test_client.patch(
        f"/agents/rooms/main/assignments/{assignment['id']}",
        headers=headers,
        json={"status": "completed", "note": "review notes attached"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"
    assert updated.json()["note"] == "review notes attached"

    room = test_client.get("/collab/rooms/main", headers=headers).json()
    assignment_messages = [
        message for message in room["messages"]
        if message["metadata"].get("source") == "agent_assignment"
    ]
    assert [message["metadata"]["status"] for message in assignment_messages] == ["queued", "completed"]
    assert assignment_messages[-1]["metadata"]["agent_label"] == "OpenAI Codex"
    assert assignment_messages[0]["metadata"]["requested_by_name"] == "Priya Nair"
    assert assignment_messages[-1]["metadata"]["updated_by_name"] == "Priya Nair"


def test_multi_agent_read_only_code_uses_reasoning_without_pending_action(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    original_app = (workspace / "app.py").read_text(encoding="utf-8")
    run = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "explain app.py", "source": "meeting"},
    )

    assert run.status_code == 200
    data = run.json()
    assert data["status"] == "completed"
    assert data["route"] == "read_only_code"
    assert data["action_id"] is None
    assert [step["role"] for step in data["steps"]] == ["coordinator", "code", "review"]
    assert data["steps"][1]["metadata"]["approval_required"] is False
    assert data["steps"][1]["metadata"]["reasoning"]["reasoning_kind"] == "read_only_code"
    assert data["steps"][1]["metadata"]["reasoning"]["files_read"] == ["app.py"]
    assert (workspace / "app.py").read_text(encoding="utf-8") == original_app

    room = test_client.get("/collab/rooms/main", headers=headers).json()
    assert room["actions"] == []


def test_agent_llm_routing_api_controls_code_agent_route(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    updated = test_client.put(
        "/agents/rooms/main/llm-routing",
        headers=headers,
        json={"role": "code", "provider": "mock", "model": "mock-deterministic"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "code"
    assert updated.json()["provider"] == "mock"
    room = test_client.get("/collab/rooms/main", headers=headers).json()
    audit_message = next(
        message for message in room["messages"]
        if message["metadata"].get("source") == "agent_llm_routing"
    )
    assert audit_message["metadata"]["updated_by_name"] == "Priya Nair"
    assert audit_message["metadata"]["role"] == "code"
    assert audit_message["metadata"]["provider"] == "mock"

    snapshot = test_client.get("/agents/rooms/main/llm-routing", headers=headers)
    assert snapshot.status_code == 200
    code_route = next(route for route in snapshot.json()["routes"] if route["role"] == "code")
    assert code_route["provider"] == "mock"
    assert code_route["model"] == "mock-deterministic"

    original_app = (workspace / "app.py").read_text(encoding="utf-8")
    run = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "explain app.py", "source": "meeting"},
    )
    assert run.status_code == 200
    data = run.json()
    assert data["route"] == "read_only_code"
    assert data["steps"][1]["metadata"]["reasoning"]["llm_route"]["route_provider"] == "mock"
    assert data["steps"][1]["metadata"]["reasoning"]["llm_route"]["provider"] == "mock"
    assert (workspace / "app.py").read_text(encoding="utf-8") == original_app


def test_agent_llm_routing_requires_approval_permission(client):
    test_client, _workspace = client
    viewer_token = _login_token(test_client, "viewer@voiceops.dev", "view123")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    response = test_client.put(
        "/agents/rooms/main/llm-routing",
        headers=headers,
        json={"role": "code", "provider": "mock"},
    )

    assert response.status_code == 403


def test_agent_room_endpoints_respect_project_room_access(client):
    test_client, _workspace = client
    admin_token = _login_token(test_client, "admin@voiceops.dev", "admin123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    scoped = test_client.put(
        "/auth/users/user-priya/projects",
        headers=admin_headers,
        json={"projects": ["project-a"]},
    )
    assert scoped.status_code == 200

    priya_token = _token(test_client)
    priya_headers = {"Authorization": f"Bearer {priya_token}"}
    assert test_client.post(
        "/collab/rooms/room-a/join",
        headers=priya_headers,
        json={"room_name": "Room A", "project": "project-a"},
    ).status_code == 200
    assert test_client.post(
        "/collab/rooms/room-b/join",
        headers=admin_headers,
        json={"room_name": "Room B", "project": "project-b"},
    ).status_code == 200

    allowed = test_client.get("/agents/rooms/room-a/llm-routing", headers=priya_headers)
    assert allowed.status_code == 200

    blocked_read = test_client.get("/agents/rooms/room-b/llm-routing", headers=priya_headers)
    assert blocked_read.status_code == 403

    blocked_write = test_client.post(
        "/agents/rooms/room-b/assignments",
        headers=priya_headers,
        json={
            "agent_id": "agent-voiceops",
            "agent_label": "VoiceOps",
            "agent_kind": "internal",
            "task": "Summarize this room",
            "mode": "memory",
        },
    )
    assert blocked_write.status_code == 403


def test_agent_assignment_cancel_retry_and_clear_completed(client):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    created = test_client.post(
        "/agents/rooms/main/assignments",
        headers=headers,
        json={
            "agent_id": "codex",
            "agent_label": "OpenAI Codex",
            "agent_kind": "external",
            "task": "Review the pending health check patch before approval",
            "mode": "review",
        },
    )
    assert created.status_code == 200
    assignment_id = created.json()["id"]

    cancelled = test_client.post(
        f"/agents/rooms/main/assignments/{assignment_id}/cancel",
        headers=headers,
        json={"reason": "duplicate queue item"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["requested_by_name"] == "Priya Nair"
    assert cancelled.json()["metadata"]["cancelled_reason"] == "duplicate queue item"
    assert cancelled.json()["metadata"]["cancelled_by_name"] == "Priya Nair"

    retried = test_client.post(
        f"/agents/rooms/main/assignments/{assignment_id}/retry",
        headers=headers,
    )
    assert retried.status_code == 200
    retry = retried.json()
    assert retry["id"] != assignment_id
    assert retry["status"] == "queued"
    assert retry["metadata"]["retry_of"] == assignment_id
    assert retry["metadata"]["original_status"] == "cancelled"
    assert retry["metadata"]["retried_by_name"] == "Priya Nair"
    assert retry["metadata"]["retry_of_requested_by_name"] == "Priya Nair"

    cleared = test_client.post("/agents/rooms/main/assignments/clear-completed", headers=headers)
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["cleared_count"] == 1
    assert [item["id"] for item in body["assignments"]] == [retry["id"]]

    room = test_client.get("/collab/rooms/main", headers=headers).json()
    assignment_messages = [
        message for message in room["messages"]
        if message["metadata"].get("source") == "agent_assignment"
    ]
    assert [message["metadata"]["status"] for message in assignment_messages] == [
        "queued",
        "cancelled",
        "queued",
        "cleared",
    ]
    assert assignment_messages[1]["metadata"]["cancelled_by_name"] == "Priya Nair"
    assert assignment_messages[2]["metadata"]["retried_by_name"] == "Priya Nair"
    assert assignment_messages[3]["metadata"]["cleared_by_name"] == "Priya Nair"
    assert assignment_messages[3]["metadata"]["cleared_assignment_summaries"][0]["requested_by_name"] == "Priya Nair"


def test_agent_assignment_retry_rejects_active_item(client):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    created = test_client.post(
        "/agents/rooms/main/assignments",
        headers=headers,
        json={
            "agent_id": "agent-voiceops",
            "agent_label": "VoiceOps",
            "agent_kind": "internal",
            "task": "Summarize Alice changes",
            "mode": "memory",
        },
    )
    assert created.status_code == 200
    retried = test_client.post(
        f"/agents/rooms/main/assignments/{created.json()['id']}/retry",
        headers=headers,
    )
    assert retried.status_code == 409
    assert "completed, failed, or cancelled" in retried.json()["detail"]


def test_agent_assignment_dispatches_internal_multi_agent_run(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    seed = test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "Open task: fix the missing health check in app.py", "source": "meeting_audio"},
    )
    assert seed.status_code == 200

    created = test_client.post(
        "/agents/rooms/main/assignments",
        headers=headers,
        json={
            "agent_id": "agent-voiceops",
            "agent_label": "VoiceOps",
            "agent_kind": "internal",
            "task": "AI fix that",
            "mode": "patch",
        },
    )
    assert created.status_code == 200
    assignment_id = created.json()["id"]

    dispatched = test_client.post(
        f"/agents/rooms/main/assignments/{assignment_id}/dispatch",
        headers=headers,
    )
    assert dispatched.status_code == 200
    data = dispatched.json()
    assert data["status"] == "completed"
    assert data["run_id"]
    assert data["action_id"]
    assert data["metadata"]["run_route"] == "meeting_patch_closure"
    assert data["metadata"]["dispatched_by_name"] == "Priya Nair"
    assert (workspace / "app.py").read_text(encoding="utf-8").find('@app.get("/health")') == -1

    room = test_client.get("/collab/rooms/main", headers=headers).json()
    action = next(item for item in room["actions"] if item["id"] == data["action_id"])
    assert action["status"] == "pending_approval"


def test_multi_agent_review_can_request_revision_before_pending_action(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    seed = test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={
            "text": "Open task: fix the missing health check in app.py and include a review note",
            "source": "meeting_audio",
        },
    )
    assert seed.status_code == 200

    original_app = (workspace / "app.py").read_text(encoding="utf-8")
    run = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "AI fix that", "source": "meeting"},
    )

    assert run.status_code == 200
    data = run.json()
    assert data["status"] == "completed"
    assert [step["role"] for step in data["steps"]] == [
        "coordinator",
        "meeting",
        "memory",
        "code",
        "review",
        "code",
        "review",
        "test",
        "git",
    ]
    assert any(finding["title"] == "Revision requested" for finding in data["findings"])
    exchange_pairs = [(item["from_role"], item["to_role"], item["title"]) for item in data["exchanges"]]
    assert ("review", "code", "Revision requested") in exchange_pairs
    assert ("code", "review", "Revised proposal sent for review") in exchange_pairs
    assert (workspace / "app.py").read_text(encoding="utf-8") == original_app

    room = test_client.get("/collab/rooms/main", headers=headers).json()
    action = next(item for item in room["actions"] if item["id"] == data["action_id"])
    assert action["status"] == "pending_approval"
    assert action["files_changed"] == ["app.py", "README.md"]
    assert action["approval"]["revision_count"] == 1
    assert action["approval"]["preapproval_test"]["passed"] is True
    assert "README.md" in action["approval"]["diff"]
    assert action["approval"]["review"]["reason"] == "missing_review_note"
    assert not (workspace / "README.md").exists()


def test_multi_agent_review_blocks_when_code_agent_has_no_pending_diff(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    (workspace / "app.py").write_text(
        textwrap.dedent(
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/health")
            def health():
                return {"status": "ok"}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    run = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "AI fix that", "source": "meeting"},
    )

    assert run.status_code == 200
    data = run.json()
    assert data["status"] == "failed"
    assert data["action_id"] is None
    assert data["findings"][-1]["title"] == "Proposal blocked"
    room = test_client.get("/collab/rooms/main", headers=headers).json()
    assert room["actions"] == []


def test_multi_agent_test_agent_blocks_failing_proposed_patch(client):
    test_client, workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    (workspace / "test_unrelated.py").write_text(
        "def test_unrelated_failure():\n    assert False\n",
        encoding="utf-8",
    )

    seed = test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "Open task: fix the missing health check in app.py", "source": "meeting_audio"},
    )
    assert seed.status_code == 200

    run = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "AI fix that", "source": "meeting"},
    )

    assert run.status_code == 200
    data = run.json()
    assert data["status"] == "failed"
    assert data["action_id"] is None
    assert data["steps"][-1]["role"] == "test"
    assert data["steps"][-1]["status"] == "failed"
    assert data["findings"][-1]["title"] == "Pre-approval tests failed"
    room = test_client.get("/collab/rooms/main", headers=headers).json()
    assert room["actions"] == []


def test_multi_agent_runs_are_listable(client):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    test_client.post("/collab/rooms/main/join", headers=headers, json={})

    response = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "what did we decide?", "source": "manual"},
    )
    assert response.status_code == 200
    assert response.json()["exchanges"][0]["from_role"] == "coordinator"
    assert response.json()["exchanges"][0]["to_role"] == "memory"

    runs = test_client.get("/agents/rooms/main/runs", headers=headers)
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["id"] == response.json()["id"]


def test_multi_agent_run_stops_when_step_budget_is_exhausted(client):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    response = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={
            "prompt": "what did we decide?",
            "source": "manual",
            "max_steps": 1,
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["status"] == "budget_exhausted"
    assert data["metadata"]["control_result"]["max_steps"] == 1
    assert data["findings"][-1]["title"] == "Run budget exhausted"
    assert len(data["steps"]) == 1


def test_multi_agent_run_times_out_before_work_starts(client):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200

    response = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={
            "prompt": "what did we decide?",
            "source": "manual",
            "timeout_seconds": 0,
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["status"] == "timed_out"
    assert data["steps"] == []
    assert data["metadata"]["control_result"]["timeout_seconds"] == 0
    assert data["findings"][-1]["title"] == "Run timed out"


def test_background_multi_agent_run_can_be_cancelled(client, monkeypatch):
    test_client, _workspace = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    agents = test_client.agent_service

    async def slow_execute(_room_id, _user, run):
        import asyncio
        await asyncio.sleep(30)
        return run

    monkeypatch.setattr(agents, "_execute", slow_execute)
    started = test_client.post(
        "/agents/rooms/main/runs",
        headers=headers,
        json={"prompt": "AI fix that", "source": "meeting", "background": True},
    )
    run_id = started.json()["id"]

    cancelled = test_client.post(
        f"/agents/rooms/main/runs/{run_id}/cancel",
        headers=headers,
        json={"reason": "user stopped runaway agent"},
    )
    data = cancelled.json()

    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert cancelled.status_code == 200
    assert data["status"] == "cancelled"
    assert data["metadata"]["control_result"]["cancelled_by_name"] == "Priya Nair"
    assert "runaway" in data["summary"]


def test_agent_service_recovers_running_runs_on_startup(client):
    test_client, _workspace = client
    path = test_client.agent_runs_path
    store = AgentRunStore(path)
    now = datetime.now(timezone.utc)
    orphan = AgentRun(
        id="run-orphaned",
        room_id="main",
        prompt="AI fix that",
        source="meeting",
        requested_by="user-priya",
        requested_by_name="Priya Nair",
        status=AgentRunStatus.RUNNING,
        summary="Coordinator is assigning specialist agents.",
        created_at=now,
        updated_at=now,
        route="meeting_patch_closure",
        metadata={},
    )
    store.append(orphan)

    recovered_service = MultiAgentService(
        AgentRunStore(path),
        test_client.agent_service._collab,
        test_client.agent_service._settings,
    )
    recovered = recovered_service.get_run("main", "run-orphaned")

    assert recovered is not None
    assert recovered.status == AgentRunStatus.RECOVERED
    assert recovered.metadata["recovered"] is True
    assert recovered.findings[-1].title == "Run recovered"


def test_agent_run_store_writes_private_audit_file(tmp_path):
    path = tmp_path / "agent-runs.json"
    store = AgentRunStore(path)
    now = datetime.now(timezone.utc)

    store.append(
        AgentRun(
            id="run-private",
            room_id="main",
            prompt="AI fix that",
            source="meeting",
            requested_by="user-priya",
            requested_by_name="Priya Nair",
            status=AgentRunStatus.COMPLETED,
            summary="Patch proposed.",
            created_at=now,
            updated_at=now,
            route="meeting_patch_closure",
            metadata={"resolved_context": "fix app.py"},
        )
    )

    assert path.stat().st_mode & 0o777 == 0o600


def test_agent_run_store_tightens_existing_audit_file(tmp_path):
    path = tmp_path / "agent-runs.json"
    path.write_text('{"runs":{},"assignments":{}}', encoding="utf-8")
    path.chmod(0o644)

    AgentRunStore(path)

    assert path.stat().st_mode & 0o777 == 0o600
