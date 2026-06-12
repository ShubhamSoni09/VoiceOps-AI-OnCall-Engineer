import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.config import Settings, get_settings
from app.integrations.github.store import get_github_token_store
from app.main import app


@pytest.fixture
def client(tmp_path):
    get_settings.cache_clear()
    get_user_store.cache_clear()
    get_github_token_store.cache_clear()

    test_settings = Settings(
        data_dir=tmp_path,
        jwt_secret="test-secret-key",
        memory_store_path="memory.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="whisper",
    )

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
