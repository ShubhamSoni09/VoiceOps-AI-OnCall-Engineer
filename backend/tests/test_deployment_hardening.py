from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.sqlite_store import SQLiteCollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.system.deployment import build_deployment_hardening_report


@pytest.fixture
def client(tmp_path):
    users_path = tmp_path / "users.json"
    store = UserStore(users_path)
    test_settings = Settings(
        users_store_path=users_path,
        jwt_secret="test-secret-key-for-deployment-hardening",
        collab_store_path=tmp_path / "collab.json",
        memory_store_path=str(tmp_path / "memory.json"),
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
    )

    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_deployment_hardening_flags_unsafe_default_environment(tmp_path):
    settings = Settings(
        deployment_environment="production",
        jwt_secret="change-me-in-production-voiceops",
        cors_allowed_origins=["*"],
        collab_store_backend="json",
        speaker_store_backend="json",
        speaker_provider="mock",
        users_store_path=tmp_path / "users.json",
        seed_demo_users=True,
        voiceops_workspace=None,
        external_agent_credential_secret=None,
    )

    report = build_deployment_hardening_report(settings)
    checks = {check.id: check for check in report.checks}

    assert report.ready is False
    assert report.status == "needs_attention"
    assert checks["jwt_secret"].ready is False
    assert checks["default_users"].ready is False
    assert checks["event_store_readiness"].ready is False
    assert checks["workspace_git"].ready is False
    assert checks["startup_security_gate"].ready is False
    assert checks["startup_security_gate"].severity == "critical"
    assert checks["startup_security_gate"].evidence["startup_gate"] == "blocked"
    assert any("Set VOICEOPS_WORKSPACE" in step for step in report.next_steps)
    assert any("prepare_production_trial.py" in command for command in report.commands)


def test_deployment_hardening_accepts_hardened_trial_config(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    (workspace / "app.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = runtime_dir / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    collab_sqlite = runtime_dir / "collaboration.sqlite3"
    SQLiteCollaborationStore(collab_sqlite)
    settings = Settings(
        deployment_environment="production",
        jwt_secret="x" * 40,
        cors_allowed_origins=["https://voiceops.example.com"],
        collab_store_backend="sqlite",
        collab_sqlite_path=collab_sqlite,
        speaker_store_backend="sqlite",
        speaker_provider="whisperx",
        hf_token="hf_test_token",
        voiceops_workspace=str(workspace),
        users_store_path=users_path,
        seed_demo_users=False,
        external_agent_credential_secret="y" * 40,
        external_agent_oauth_mock_enabled=False,
        github_pr_creation_enabled=False,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_deployment_hardening_report(settings)
    checks = {check.id: check for check in report.checks}

    assert report.ready is True
    assert report.status == "ready"
    assert checks["workspace_git"].ready is True
    assert checks["event_store_readiness"].ready is True
    assert checks["bootstrap_secret_file"].ready is True
    assert checks["startup_security_gate"].ready is True
    assert checks["startup_security_gate"].evidence["startup_gate"] == "passed"


def test_deployment_hardening_blocks_world_readable_credential_store(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = runtime_dir / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    external_credentials = runtime_dir / "external_agent_credentials.json"
    external_credentials.write_text("[]", encoding="utf-8")
    external_credentials.chmod(0o644)
    collab_sqlite = runtime_dir / "collaboration.sqlite3"
    SQLiteCollaborationStore(collab_sqlite)
    settings = Settings(
        deployment_environment="production",
        jwt_secret="x" * 40,
        cors_allowed_origins=["https://voiceops.example.com"],
        collab_store_backend="sqlite",
        collab_sqlite_path=collab_sqlite,
        speaker_store_backend="sqlite",
        speaker_provider="whisperx",
        hf_token="hf_test_token",
        voiceops_workspace=str(workspace),
        users_store_path=users_path,
        seed_demo_users=False,
        external_agent_credential_secret="y" * 40,
        github_pr_creation_enabled=False,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=external_credentials,
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_deployment_hardening_report(settings)
    runtime_paths = next(check for check in report.checks if check.id == "runtime_artifact_paths")

    assert report.ready is False
    assert runtime_paths.ready is False
    assert runtime_paths.evidence["artifacts"]["external_agent_credentials"]["mode"] == "0o644"
    assert "owner-readable only" in runtime_paths.detail


def test_deployment_hardening_blocks_disabled_startup_gate(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = runtime_dir / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    settings = Settings(
        deployment_environment="production",
        production_startup_security_gate=False,
        jwt_secret="x" * 40,
        cors_allowed_origins=["https://voiceops.example.com"],
        collab_store_backend="sqlite",
        speaker_store_backend="sqlite",
        speaker_provider="whisperx",
        hf_token="hf_test_token",
        voiceops_workspace=str(workspace),
        users_store_path=users_path,
        seed_demo_users=False,
        external_agent_credential_secret="y" * 40,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_deployment_hardening_report(settings)
    gate = next(check for check in report.checks if check.id == "startup_security_gate")

    assert report.ready is False
    assert gate.ready is False
    assert gate.evidence["production_startup_security_gate"] is False
    assert "PRODUCTION_STARTUP_SECURITY_GATE" in gate.detail


def test_deployment_hardening_blocks_bootstrap_password_file(tmp_path):
    runtime_dir = tmp_path / "trial" / "data"
    runtime_dir.mkdir(parents=True)
    users_path = runtime_dir / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    bootstrap_path = tmp_path / "trial" / "bootstrap-admin.txt"
    bootstrap_path.write_text("temporary_password=secret", encoding="utf-8")
    settings = Settings(users_store_path=users_path)

    report = build_deployment_hardening_report(settings)
    bootstrap = next(check for check in report.checks if check.id == "bootstrap_secret_file")

    assert bootstrap.ready is False
    assert bootstrap.severity == "critical"
    assert bootstrap.evidence["path"] == str(bootstrap_path)
    assert "delete bootstrap-admin.txt" in bootstrap.action


def test_deployment_hardening_api_requires_admin(client):
    on_call = client.get(
        "/system/deployment/hardening",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    admin = client.get(
        "/system/deployment/hardening",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )

    assert on_call.status_code == 403
    assert admin.status_code == 200
    assert "checks" in admin.json()


def _token(client) -> str:
    res = client.post(
        "/auth/login",
        json={"email": "priya@voiceops.dev", "password": "oncall123"},
    )
    return res.json()["access_token"]


def _admin_token(client) -> str:
    res = client.post(
        "/auth/login",
        json={"email": "admin@voiceops.dev", "password": "admin123"},
    )
    return res.json()["access_token"]
