import pytest
from fastapi.testclient import TestClient

from app.auth.users import UserStore
from app.config import Settings, get_settings
from app.auth.dependencies import get_user_store
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
        voiceops_workspace=None,
    )

    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _token(client) -> str:
    res = client.post(
        "/auth/login",
        json={"email": "priya@voiceops.dev", "password": "oncall123"},
    )
    return res.json()["access_token"]


def test_bootstrap_disconnected(client):
    res = client.get(
        "/console/bootstrap",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["workspace"]["connected"] is False
    assert data["incidents"] == []
    assert data["metrics"] == []
    assert data["artifacts"] == []
    assert data["status_counts"] == {"critical": 0, "active": 0}
    assert data["user"]["email"] == "priya@voiceops.dev"
    assert any(i["id"] == "mcp" and not i["connected"] for i in data["integrations"])


def test_bootstrap_connected_workspace(client, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo service\n", encoding="utf-8")

    test_settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        llm_provider="mock",
        voiceops_workspace=str(workspace),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings

    res = client.get(
        "/console/bootstrap",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["workspace"]["connected"] is True
    assert data["workspace"]["name"] == "repo"
    assert data["workspace"]["readme_line"] == "# Demo service"
    assert any(i["id"] == "mcp" and i["connected"] for i in data["integrations"])
