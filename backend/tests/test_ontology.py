from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.models import UserPublic
from app.auth.users import UserStore
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.voice_agent.models import OrchestratorResult
import app.voice_agent.router as voice_router


@pytest.fixture
def client(tmp_path):
    users_path = tmp_path / "users.json"
    users = UserStore(users_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    settings = Settings(
        users_store_path=users_path,
        jwt_secret="ontology-test-secret",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        rag_index_path=tmp_path / "rag-index.json",
        voiceops_cache_path=tmp_path / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        voiceops_workspace=None,
    )

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


def _token(client: TestClient) -> str:
    response = client.post("/auth/login", json={"email": "priya@voiceops.dev", "password": "oncall123"})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_room_ontology_links_people_files_actions_and_branches(client):
    test_client, collab = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    assert test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "Alice decided app.py owns the health route", "source": "meeting_audio"},
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
            summary="Priya patched app.py health route.",
            files_changed=["app.py"],
            approval={
                "status": "approved",
                "decided_by": "user-admin",
                "decided_by_name": "Sam Ortiz",
                "git": {"branch_name": "voiceops/act-test-health", "files_changed": ["app.py"]},
            },
        ),
    )

    response = test_client.get("/ontology/rooms/main", headers=headers)

    assert response.status_code == 200
    graph = response.json()
    node_ids = {node["id"] for node in graph["nodes"]}
    edge_relations = {edge["relation"] for edge in graph["edges"]}
    assert "file:app.py" in node_ids
    assert "person:user-priya" in node_ids
    assert "person:user-admin" in node_ids
    assert "branch:voiceops/act-test-health" in node_ids
    assert {"mentioned_file", "touches_file", "approved_action", "created_branch"}.issubset(edge_relations)
    assert graph["summary"]["file"] >= 1
    assert graph["summary"]["edge:touches_file"] >= 1


def test_ontology_query_answers_file_context(client):
    test_client, _collab = client
    token = _token(test_client)
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
    assert test_client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "Bob mentioned README.md and app.py in the handoff", "source": "meeting_audio"},
    ).status_code == 200

    response = test_client.get("/ontology/rooms/main/query", headers=headers, params={"q": "what files are in context?"})

    assert response.status_code == 200
    data = response.json()
    assert "Files in project context:" in data["answer"]
    assert {node["label"] for node in data["nodes"]} >= {"README.md", "app.py"}
    assert data["mode"] == "deterministic"
