import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.integrations.github.api import GitHubApiError, parse_github_repo
from app.main import app


def test_parse_github_repo():
    assert parse_github_repo("acme/checkout-api") == ("acme", "checkout-api")
    assert parse_github_repo("https://github.com/acme/checkout-api.git") == ("acme", "checkout-api")


def test_parse_github_repo_invalid():
    with pytest.raises(GitHubApiError):
        parse_github_repo("not-a-repo")


def test_github_login_url_when_not_configured(tmp_path):
    get_settings.cache_clear()
    app.dependency_overrides[get_settings] = lambda: Settings(
        data_dir=tmp_path,
        jwt_secret="test",
        llm_provider="mock",
    )
    with TestClient(app) as client:
        res = client.get("/auth/github/login-url")
        assert res.status_code == 503
    app.dependency_overrides.clear()


def test_github_login_url_when_configured(tmp_path):
    get_settings.cache_clear()
    app.dependency_overrides[get_settings] = lambda: Settings(
        data_dir=tmp_path,
        jwt_secret="test",
        llm_provider="mock",
        github_client_id="test-client",
        github_client_secret="secret",
        github_callback_url="http://test/auth/github/callback",
    )
    with TestClient(app) as client:
        res = client.get("/auth/github/login-url")
        assert res.status_code == 200
        assert "github.com/login/oauth/authorize" in res.json()["url"]
    app.dependency_overrides.clear()
