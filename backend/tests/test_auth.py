import json

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.models import Role, UserRecord
from app.auth.security import hash_password
from app.auth.users import UserStore
from app.config import Settings, get_settings
from app.main import app
import app.voice_agent.router as voice_router


@pytest.fixture
def client(tmp_path):
    users_path = tmp_path / "users.json"
    store = UserStore(users_path)

    test_settings = Settings(
        users_store_path=users_path,
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
    )

    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: test_settings
    voice_router._pipeline = None
    voice_router._pipeline_settings = None

    with TestClient(app) as test_client:
        yield test_client

    voice_router._pipeline = None
    voice_router._pipeline_settings = None
    app.dependency_overrides.clear()


def test_login_success(client):
    res = client.post(
        "/auth/login",
        json={"email": "priya@voiceops.dev", "password": "oncall123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "on_call"
    assert "voice:use" in data["user"]["permissions"]
    assert "agent:run" in data["user"]["permissions"]
    assert "agent:approve" in data["user"]["permissions"]
    assert "credentials:manage_own" in data["user"]["permissions"]


def test_login_exposes_project_allowlist(client):
    store = app.dependency_overrides[get_user_store]()
    store._users["alpha@voiceops.dev"] = UserRecord(
        id="user-alpha",
        email="alpha@voiceops.dev",
        name="Alpha Engineer",
        initials="AE",
        role=Role.ON_CALL,
        password_hash=hash_password("alpha123"),
        projects=["alpha"],
    )

    res = client.post("/auth/login", json={"email": "alpha@voiceops.dev", "password": "alpha123"})

    assert res.status_code == 200
    assert res.json()["user"]["projects"] == ["alpha"]


def test_admin_can_manage_user_project_access(client):
    admin = _token(client, "admin@voiceops.dev", "admin123")
    priya = _token(client, "priya@voiceops.dev", "oncall123")

    forbidden = client.put(
        "/auth/users/user-priya/projects",
        headers={"Authorization": f"Bearer {priya}"},
        json={"projects": ["alpha"]},
    )
    assert forbidden.status_code == 403

    updated = client.put(
        "/auth/users/user-priya/projects",
        headers={"Authorization": f"Bearer {admin}"},
        json={"projects": [" alpha ", "alpha", "", "beta"]},
    )
    assert updated.status_code == 200
    assert updated.json()["projects"] == ["alpha", "beta"]

    listed = client.get("/auth/users", headers={"Authorization": f"Bearer {admin}"})
    assert listed.status_code == 200
    priya_record = next(user for user in listed.json() if user["id"] == "user-priya")
    assert priya_record["projects"] == ["alpha", "beta"]


def test_admin_project_access_update_controls_room_join(client):
    admin = _token(client, "admin@voiceops.dev", "admin123")
    priya = _token(client, "priya@voiceops.dev", "oncall123")

    update = client.put(
        "/auth/users/user-priya/projects",
        headers={"Authorization": f"Bearer {admin}"},
        json={"projects": ["alpha"]},
    )
    assert update.status_code == 200

    denied = client.post(
        "/collab/rooms/beta-room/join",
        headers={"Authorization": f"Bearer {priya}"},
        json={"room_name": "Beta room", "project": "beta"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/collab/rooms/alpha-room/join",
        headers={"Authorization": f"Bearer {priya}"},
        json={"room_name": "Alpha room", "project": "alpha"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["room"]["project"] == "alpha"


def test_user_store_can_disable_demo_user_seed(tmp_path):
    users_path = tmp_path / "users.json"

    store = UserStore(users_path, seed_demo_users=False)

    assert store.get_by_email("priya@voiceops.dev") is None
    assert json.loads(users_path.read_text(encoding="utf-8")) == {"users": []}


def test_user_store_writes_private_password_hash_file(tmp_path):
    users_path = tmp_path / "users.json"

    UserStore(users_path)

    assert users_path.stat().st_mode & 0o777 == 0o600


def test_user_store_tightens_existing_password_hash_file(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o644)

    UserStore(users_path)

    assert users_path.stat().st_mode & 0o777 == 0o600


def test_login_invalid_password(client):
    res = client.post(
        "/auth/login",
        json={"email": "priya@voiceops.dev", "password": "wrong"},
    )
    assert res.status_code == 401


def test_viewer_cannot_use_voice_api(client):
    login = client.post(
        "/auth/login",
        json={"email": "viewer@voiceops.dev", "password": "view123"},
    )
    token = login.json()["access_token"]
    res = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "check api status", "session_id": "test"},
    )
    assert res.status_code == 403


def test_on_call_can_use_voice_api(client):
    login = client.post(
        "/auth/login",
        json={"email": "priya@voiceops.dev", "password": "oncall123"},
    )
    token = login.json()["access_token"]
    res = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "check api status", "session_id": "test"},
    )
    assert res.status_code == 200


def test_voice_health_omits_sensitive_runtime_details(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    workspace = tmp_path / "private-workspace"
    workspace.mkdir()
    settings.voiceops_workspace = str(workspace)
    settings.llm_provider = "openai_compatible"
    settings.openai_compatible_base_url = "http://127.0.0.1:8000/v1"
    settings.openai_compatible_model = "glm-test"

    res = client.get("/voice/health")

    assert res.status_code == 200
    data = res.json()
    assert data["workspace_connected"] is True
    assert data["llm_ready"] is True
    assert "workspace" not in data
    assert "openai_compatible_base_url" not in data
    assert "openai_compatible_model" not in data


def test_voice_sessions_are_scoped_by_user(client):
    priya = _token(client, "priya@voiceops.dev", "oncall123")
    admin = _token(client, "admin@voiceops.dev", "admin123")

    first = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {priya}"},
        json={"text": "Check API status", "session_id": "shared"},
    )
    second = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {admin}"},
        json={"text": "Now investigate errors", "session_id": "shared"},
    )
    third = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {priya}"},
        json={"text": "Now investigate errors", "session_id": "shared"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert second.json()["session_id"] == "shared"
    assert "Check API status" not in _history_text(second)
    assert "Check API status" in _history_text(third)


def test_voice_clear_session_only_clears_callers_scoped_session(client):
    priya = _token(client, "priya@voiceops.dev", "oncall123")
    admin = _token(client, "admin@voiceops.dev", "admin123")

    client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {priya}"},
        json={"text": "Check API status", "session_id": "shared"},
    )
    client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {admin}"},
        json={"text": "Admin check API status", "session_id": "shared"},
    )

    cleared = client.delete("/voice/sessions/shared", headers={"Authorization": f"Bearer {priya}"})
    priya_after_clear = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {priya}"},
        json={"text": "Now investigate errors", "session_id": "shared"},
    )
    admin_after_clear = client.post(
        "/voice/process-text",
        headers={"Authorization": f"Bearer {admin}"},
        json={"text": "Now investigate errors", "session_id": "shared"},
    )

    assert cleared.status_code == 200
    assert "Check API status" not in _history_text(priya_after_clear)
    assert "Admin check API status" in _history_text(admin_after_clear)


def test_viewer_cannot_manage_external_agent_credentials(client):
    login = client.post(
        "/auth/login",
        json={"email": "viewer@voiceops.dev", "password": "view123"},
    )
    token = login.json()["access_token"]
    res = client.post(
        "/external-agents/providers/claude/credentials/api-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "sk-test-secret-value-123456"},
    )
    assert res.status_code == 403


def test_on_call_can_manage_own_external_agent_credentials(client):
    login = client.post(
        "/auth/login",
        json={"email": "priya@voiceops.dev", "password": "oncall123"},
    )
    token = login.json()["access_token"]
    res = client.post(
        "/external-agents/providers/claude/credentials/api-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"api_key": "sk-test-secret-value-123456"},
    )
    assert res.status_code == 200


def _token(client, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _history_text(response) -> str:
    return "\n".join(turn["content"] for turn in response.json()["context"]["conversation_history"])
