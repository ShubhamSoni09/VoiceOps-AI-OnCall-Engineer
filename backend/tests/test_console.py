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
        voiceops_workspace=None,
    )

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
    (workspace / "app.py").write_text(
        'app = None\nmetrics = {"error_rate_pct": 1}\n',
        encoding="utf-8",
    )

    test_settings = Settings(
        data_dir=tmp_path,
        jwt_secret="test-secret-key",
        memory_store_path="memory.json",
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
    assert len(data["incidents"]) == 4
    assert data["status_counts"]["active"] == 3
    assert data["status_counts"]["critical"] == 1
    assert len(data["metrics"]) == 4
    assert len(data["artifacts"]) >= 1
    assert any(i["id"] == "mcp" and i["connected"] for i in data["integrations"])
