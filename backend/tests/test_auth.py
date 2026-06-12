import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client(tmp_path):
    users_path = tmp_path / "users.json"
    store = UserStore(users_path)

    test_settings = Settings(
        users_store_path=users_path,
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="whisper",
    )

    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as test_client:
        yield test_client

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
