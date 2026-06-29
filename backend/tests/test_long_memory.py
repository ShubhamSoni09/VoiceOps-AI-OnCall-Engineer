from __future__ import annotations

import subprocess
import textwrap

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.models import Role, UserPublic, UserRecord
from app.auth.security import hash_password
from app.auth.users import UserStore
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.voice_agent.models import OrchestratorResult
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
        'from app import app\n\n\ndef test_health_route_exists():\n    assert "/health" in [route.path for route in app.routes]\n',
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
        jwt_secret="long-memory-test-secret",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        long_memory_path=tmp_path / "long-memory.json",
        rag_index_path=tmp_path / "rag-index.json",
        voiceops_cache_path=tmp_path / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(users_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab

    with TestClient(app) as test_client:
        yield test_client, collab

    voice_router._pipeline = None
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


def _token(client: TestClient, email="priya@voiceops.dev", password="oncall123") -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _outsider_token(client: TestClient) -> str:
    store = app.dependency_overrides[get_user_store]()
    store._users["long-outsider@voiceops.dev"] = UserRecord(
        id="user-long-outsider",
        email="long-outsider@voiceops.dev",
        name="Long Memory Outsider",
        initials="LO",
        role=Role.ON_CALL,
        password_hash=hash_password("outsider123"),
    )
    return _token(client, "long-outsider@voiceops.dev", "outsider123")


def test_archive_room_creates_long_memory_for_decisions_actions_and_handoff(client):
    test_client, collab = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    assert test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "We decided app.py owns the health check", "source": "meeting_audio"},
    ).status_code == 200

    user = UserPublic(
        id="user-priya",
        email="priya@voiceops.dev",
        name="Priya Nair",
        initials="PN",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
    collab.add_action(
        "main",
        user,
        OrchestratorResult(
            executed=True,
            action="patch",
            summary="Priya approved app.py health patch.",
            files_changed=["app.py"],
            approval={"status": "approved", "decided_by_name": "Sam Ortiz"},
        ),
    )

    archive = test_client.post("/memory/rooms/main/archive", headers=headers)
    repeat = test_client.post("/memory/rooms/main/archive", headers=headers)
    records = test_client.get("/memory/rooms/main/long", headers=headers)

    assert archive.status_code == 200
    assert repeat.status_code == 200
    assert repeat.json()["archived_count"] == 0
    assert records.status_code == 200
    kinds = {record["kind"] for record in records.json()}
    assert "memory:decision" in kinds
    assert "action:approved_patch" in kinds
    assert "handoff" in kinds
    assert test_client.app.dependency_overrides[get_settings]().long_memory_path.stat().st_mode & 0o777 == 0o600


def test_long_memory_room_endpoints_require_membership(client):
    test_client, _collab = client
    member = _token(test_client)
    outsider = _outsider_token(test_client)
    member_headers = {"Authorization": f"Bearer {member}"}
    outsider_headers = {"Authorization": f"Bearer {outsider}"}
    assert test_client.post("/collab/rooms/main/join", headers=member_headers, json={}).status_code == 200

    for method, path in [
        ("POST", "/memory/rooms/main/archive"),
        ("GET", "/memory/rooms/main/long"),
        ("GET", "/memory/rooms/main/long/query?q=app.py"),
    ]:
        response = test_client.request(method, path, headers=outsider_headers)
        assert response.status_code == 403
        assert "Join this room" in response.json()["detail"]


def test_approved_patch_auto_archives_long_memory(client):
    test_client, collab = client
    alice = _token(test_client)
    bob = _token(test_client, "admin@voiceops.dev", "admin123")
    alice_headers = {"Authorization": f"Bearer {alice}"}
    bob_headers = {"Authorization": f"Bearer {bob}"}
    assert test_client.post("/collab/rooms/main/join", headers=alice_headers, json={}).status_code == 200

    user = UserPublic(
        id="user-priya",
        email="priya@voiceops.dev",
        name="Priya Nair",
        initials="PN",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
    action = collab.add_action(
        "main",
        user,
        OrchestratorResult(
            executed=False,
            action="patch",
            summary="Patch app.py health endpoint.",
            files_changed=["app.py"],
            pending_approval=True,
            approval={
                "kind": "patch",
                "status": "pending_approval",
                "diff": "--- a/app.py\n+++ b/app.py\n",
                "test_command": "python -m pytest -q",
            },
            approval_payload={
                "kind": "patch",
                "files": {
                    "app.py": 'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'
                },
                "test_command": "python -m pytest -q",
            },
        ),
    )
    assert action is not None

    approved = test_client.post(f"/collab/rooms/main/actions/{action.id}/approve", headers=bob_headers, json={})
    query = test_client.get(
        "/memory/rooms/main/long/query",
        headers=bob_headers,
        params={"q": "who approved app.py health patch?"},
    )

    assert approved.status_code == 200
    assert query.status_code == 200
    data = query.json()
    assert data["records"]
    assert data["records"][0]["kind"] == "action:approved_patch"
    assert data["citations"]
    assert data["citations"][0]["source"] == "action"
    assert data["citations"][0]["metadata"]["room_id"] == "main"
    assert data["citations"][0]["metadata"]["access_scope"] == "room"
    assert data["citations"][0]["metadata"]["source_pointer"]["action_id"] == data["records"][0]["source_id"]
    assert data["retrieval"]["access_policy"]["policy"] == "authenticated_room_context"
    assert data["retrieval"]["sources"]["action"] >= 1
    assert "Sam Ortiz approved the patch" in data["answer"]
