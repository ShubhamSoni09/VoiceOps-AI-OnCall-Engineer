import json
from pathlib import Path
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.auth.models import Role, UserPublic, UserRecord
from app.auth.security import hash_password
from app.auth.users import UserStore
from app.collab.models import JoinRoomRequest, TextMessageRequest
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.sqlite_store import SQLiteCollaborationStore
from app.collab.store import CollaborationStore
from app.config import Settings, apply_workspace_selection, get_settings
from app.auth.dependencies import get_user_store
from app.main import app
from app.speakers.service import get_speaker_service
from app.system import router as system_router
from app.system.security import StartupSecurityError, build_production_security_report, validate_startup_security
import app.workspace.github as github_module


@pytest.fixture
def client(tmp_path):
    users_path = tmp_path / "users.json"
    store = UserStore(users_path)

    test_settings = Settings(
        users_store_path=users_path,
        jwt_secret="test-secret-key",
        collab_store_path=tmp_path / "collab.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        rag_index_path=tmp_path / "rag-index.json",
        external_agent_store_path=tmp_path / "external-agents.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
        voiceops_cache_path=tmp_path / "cache.json",
        workspace_selection_path=tmp_path / "workspace-selection.json",
        workspace_clone_root=tmp_path / "clones",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        speaker_provider="mock",
        speaker_verification_path=tmp_path / "speaker_verification.json",
        demo_evidence_path=tmp_path / "demo_evidence.json",
        voiceops_workspace=None,
        collab_store_backend="json",
        speaker_store_backend="json",
    )
    collab = CollaborationService(CollaborationStore(test_settings.collab_store_path))

    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab

    with TestClient(app) as test_client:
        yield test_client

    system_router._speaker_warmup_job = None
    system_router._speaker_warmup_task = None
    system_router._speaker_verification_job = None
    system_router._speaker_verification_task = None
    system_router._demo_gate_run_job = None
    system_router._demo_gate_run_task = None
    system_router._demo_gate_completion_targets.clear()
    app.dependency_overrides.clear()


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


def _outsider_token(client) -> str:
    store = app.dependency_overrides[get_user_store]()
    store._users["observability-outsider@voiceops.dev"] = UserRecord(
        id="user-observability-outsider",
        email="observability-outsider@voiceops.dev",
        name="Observability Outsider",
        initials="OO",
        role=Role.ON_CALL,
        password_hash=hash_password("outsider123"),
    )
    res = client.post(
        "/auth/login",
        json={"email": "observability-outsider@voiceops.dev", "password": "outsider123"},
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


def test_bootstrap_reports_github_url_workspace_as_setup_issue(client, tmp_path):
    test_settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_cache_path=tmp_path / "cache.json",
        llm_provider="mock",
        voiceops_workspace="https://github.com/team/app.git",
    )
    app.dependency_overrides[get_settings] = lambda: test_settings

    res = client.get(
        "/console/bootstrap",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )

    assert res.status_code == 200
    data = res.json()
    assert data["workspace"]["connected"] is False
    assert data["workspace"]["configured_workspace"] == "https://github.com/team/app.git"
    assert "local clone path" in data["workspace"]["setup_issue"]
    mcp = next(i for i in data["integrations"] if i["id"] == "mcp")
    assert "local clone path" in mcp["detail"]


def test_bootstrap_connected_workspace(client, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Demo service\n", encoding="utf-8")

    test_settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_cache_path=tmp_path / "cache.json",
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
    github = next(i for i in data["integrations"] if i["id"] == "github")
    assert github["connected"] is False
    assert github["detail"] == "Local folder, git not initialized"


def test_bootstrap_distinguishes_local_git_from_github_remote(client, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)

    test_settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_cache_path=tmp_path / "cache.json",
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
    github = next(i for i in data["integrations"] if i["id"] == "github")
    assert data["workspace"]["is_git_repo"] is True
    assert data["workspace"]["remote_kind"] == "none"
    assert data["workspace"]["remote_web_url"] is None
    assert github["connected"] is False
    assert github["detail"] == "Local git repo, no GitHub remote"

    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:team/app.git"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )

    res = client.get(
        "/console/bootstrap",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    assert res.status_code == 200
    data = res.json()
    github = next(i for i in data["integrations"] if i["id"] == "github")
    assert data["workspace"]["remote_kind"] == "github"
    assert data["workspace"]["remote_web_url"] == "https://github.com/team/app"
    assert github["connected"] is True
    assert github["detail"] == "https://github.com/team/app"


def test_admin_can_connect_local_workspace_without_restart(client, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Connected repo\n", encoding="utf-8")
    settings = app.dependency_overrides[get_settings]()
    assert settings.voiceops_workspace is None

    res = client.post(
        "/console/workspace",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        json={"path": str(workspace)},
    )

    assert res.status_code == 200
    data = res.json()
    assert settings.voiceops_workspace == str(workspace.resolve())
    assert json.loads(settings.workspace_selection_path.read_text(encoding="utf-8"))["path"] == str(workspace.resolve())
    assert data["workspace"]["connected"] is True
    assert data["workspace"]["source"] == "runtime"
    assert "saved for restart" in data["workspace"]["persistence_note"]
    assert data["workspace"]["name"] == "repo"
    assert data["workspace"]["readme_line"] == "# Connected repo"

    bootstrap = client.get(
        "/console/bootstrap",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    assert bootstrap.json()["workspace"]["connected"] is True


def test_persisted_workspace_selection_loads_when_env_workspace_is_empty(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    selection_path = tmp_path / "workspace-selection.json"
    selection_path.write_text(json.dumps({"path": str(workspace)}), encoding="utf-8")
    settings = Settings(voiceops_workspace=None, workspace_selection_path=selection_path)

    apply_workspace_selection(settings)

    assert settings.voiceops_workspace == str(workspace)
    assert settings.voiceops_workspace_source == "saved"


def test_explicit_workspace_wins_over_persisted_selection(tmp_path):
    explicit = tmp_path / "explicit"
    selected = tmp_path / "selected"
    explicit.mkdir()
    selected.mkdir()
    selection_path = tmp_path / "workspace-selection.json"
    selection_path.write_text(json.dumps({"path": str(selected)}), encoding="utf-8")
    settings = Settings(voiceops_workspace=str(explicit), workspace_selection_path=selection_path)

    apply_workspace_selection(settings)

    assert settings.voiceops_workspace == str(explicit)
    assert settings.voiceops_workspace_source == "configured"


def test_on_call_cannot_connect_server_workspace(client, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()

    res = client.post(
        "/console/workspace",
        headers={"Authorization": f"Bearer {_token(client)}"},
        json={"path": str(workspace)},
    )

    assert res.status_code == 403


def test_connect_workspace_rejects_github_url(client):
    res = client.post(
        "/console/workspace",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        json={"path": "https://github.com/team/app.git"},
    )

    assert res.status_code == 400
    assert "local clone path" in res.json()["detail"]


def test_admin_can_clone_github_repo_and_connect_workspace(client, monkeypatch, tmp_path):
    target = tmp_path / "clones" / "app"
    settings = app.dependency_overrides[get_settings]()
    settings.workspace_clone_root = tmp_path / "clones"
    calls = []

    def fake_run(args, **_kwargs):
        if args[:2] == ["git", "clone"]:
            calls.append(args)
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "README.md").write_text("# Cloned repo\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="cloned", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    res = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        json={
            "remote_url": "https://github.com/team/app.git",
            "target_path": str(target),
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert calls == [["git", "clone", "https://github.com/team/app.git", str(target)]]
    assert settings.voiceops_workspace == str(target.resolve())
    assert json.loads(settings.workspace_selection_path.read_text(encoding="utf-8"))["path"] == str(target.resolve())
    assert data["workspace"]["connected"] is True
    assert data["workspace"]["name"] == "app"
    assert data["workspace"]["source"] == "runtime"
    assert data["workspace"]["readme_line"] == "# Cloned repo"


def test_clone_workspace_uses_default_clone_root_when_target_is_empty(client, monkeypatch, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.workspace_clone_root = tmp_path / "default-clones"
    target = settings.workspace_clone_root / "api"
    calls = []

    def fake_run(args, **_kwargs):
        if args[:2] == ["git", "clone"]:
            calls.append(args)
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            return subprocess.CompletedProcess(args, 0, stdout="cloned", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    res = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        json={"remote_url": "git@github.com:team/api.git"},
    )

    assert res.status_code == 200
    assert calls == [["git", "clone", "git@github.com:team/api.git", str(target.resolve())]]
    assert res.json()["workspace"]["path"] == str(target.resolve())


def test_clone_workspace_uses_authenticated_github_cli_for_https_repo(client, monkeypatch, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.workspace_clone_root = tmp_path / "default-clones"
    target = settings.workspace_clone_root / "app"
    calls = []

    monkeypatch.setattr(github_module.shutil, "which", lambda command: "/usr/bin/gh" if command == "gh" else None)

    def fake_run(args, **_kwargs):
        calls.append(args)
        if args == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, stdout="Logged in to github.com", stderr="")
        if args == ["gh", "repo", "clone", "team/app", str(target.resolve())]:
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            return subprocess.CompletedProcess(args, 0, stdout="cloned", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    res = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        json={"remote_url": "https://github.com/team/app.git"},
    )

    assert res.status_code == 200
    assert calls[:2] == [
        ["gh", "auth", "status"],
        ["gh", "repo", "clone", "team/app", str(target.resolve())],
    ]
    assert res.json()["workspace"]["path"] == str(target.resolve())


def test_clone_workspace_rejects_non_github_and_relative_targets(client, tmp_path):
    token = _admin_token(client)
    settings = app.dependency_overrides[get_settings]()
    settings.workspace_clone_root = tmp_path / "clones"

    bad_remote = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {token}"},
        json={"remote_url": "https://evil.example/team/app.git", "target_path": str(tmp_path / "app")},
    )
    bad_target = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {token}"},
        json={"remote_url": "git@github.com:team/app.git", "target_path": "relative/app"},
    )
    outside_target = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {token}"},
        json={"remote_url": "git@github.com:team/app.git", "target_path": str(tmp_path / "outside" / "app")},
    )

    assert bad_remote.status_code == 400
    assert "GitHub" in bad_remote.json()["detail"]
    assert bad_target.status_code == 400
    assert "absolute" in bad_target.json()["detail"]
    assert outside_target.status_code == 400
    assert "WORKSPACE_CLONE_ROOT" in outside_target.json()["detail"]


def test_clone_workspace_redacts_git_failure_secrets(client, monkeypatch, tmp_path):
    token = _admin_token(client)
    settings = app.dependency_overrides[get_settings]()
    settings.workspace_clone_root = tmp_path

    def fake_run(args, **_kwargs):
        return subprocess.CompletedProcess(
            args,
            128,
            stdout="",
            stderr="fatal: https://ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/team/app.git failed with token=sk-test-secret-value-123456",
        )

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)

    res = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {token}"},
        json={"remote_url": "https://github.com/team/app.git", "target_path": str(tmp_path / "app")},
    )
    detail = res.json()["detail"]

    assert res.status_code == 409
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in detail
    assert "sk-test-secret-value-123456" not in detail
    assert "[redacted]" in detail


def test_on_call_cannot_clone_server_workspace(client, tmp_path):
    res = client.post(
        "/console/workspace/clone",
        headers={"Authorization": f"Bearer {_token(client)}"},
        json={"remote_url": "https://github.com/team/app.git", "target_path": str(tmp_path / "app")},
    )

    assert res.status_code == 403


def test_cache_status_and_clear_permissions(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.voiceops_cache_path.write_text(
        '{"git:test": {"value": {"dirty": false}, "expires_at": 9999999999}}',
        encoding="utf-8",
    )
    on_call = _token(client)
    admin = _admin_token(client)
    viewer_login = client.post(
        "/auth/login",
        json={"email": "viewer@voiceops.dev", "password": "view123"},
    )
    viewer = viewer_login.json()["access_token"]

    status = client.get("/system/cache/status", headers={"Authorization": f"Bearer {viewer}"})
    forbidden = client.post("/system/cache/clear", headers={"Authorization": f"Bearer {viewer}"})
    forbidden_on_call = client.post("/system/cache/clear", headers={"Authorization": f"Bearer {on_call}"})
    cleared = client.post("/system/cache/clear", headers={"Authorization": f"Bearer {admin}"})

    assert status.status_code == 200
    assert status.json()["entries"] == 1
    assert status.json()["rebuildable_entries"] == 1
    assert status.json()["fingerprinted_entries"] == 0
    assert status.json()["workspace_ttl_seconds"] == 10.0
    assert forbidden.status_code == 403
    assert forbidden_on_call.status_code == 403
    assert cleared.status_code == 200
    assert cleared.json()["cleared_entries"] == 1
    assert settings.voiceops_cache_path.stat().st_mode & 0o777 == 0o600
    assert cleared.json()["entries"] == 0


def test_runtime_status_reports_storage_and_provider_readiness(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.collab_store_backend = "sqlite"
    settings.collab_sqlite_path = tmp_path / "collab.sqlite3"
    settings.collab_sqlite_path.write_text("sqlite placeholder", encoding="utf-8")
    settings.speaker_store_backend = "sqlite"
    settings.speaker_sqlite_path = tmp_path / "speakers.sqlite3"
    settings.speaker_sqlite_path.write_text("speaker placeholder", encoding="utf-8")
    settings.speaker_provider = "whisperx"
    settings.hf_token = None

    res = client.get(
        "/system/runtime/status",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    stores = {item["id"]: item for item in data["stores"]}
    providers = {item["id"]: item for item in data["providers"]}

    assert res.status_code == 200
    assert stores["collab"]["backend"] == "sqlite"
    assert stores["collab"]["exists"] is True
    assert stores["speakers"]["backend"] == "sqlite"
    assert providers["speaker"]["value"] == "whisperx"
    assert providers["speaker"]["ready"] is False
    assert data["cache"]["entries"] == 0
    assert any("HF_TOKEN" in warning for warning in data["warnings"])


def test_observability_snapshot_reports_room_agent_rag_and_provider_health(client):
    token = _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/collab/rooms/main/join",
        headers=headers,
        json={"room_name": "Ops room", "project": "demo"},
    ).status_code == 200
    assert client.post(
        "/collab/rooms/main/messages",
        headers=headers,
        json={"text": "We decided app.py needs a small follow up task.", "source": "meeting_audio"},
    ).status_code == 200

    res = client.get("/system/observability?room_id=main", headers=headers)
    data = res.json()
    components = {component["id"]: component for component in data["components"]}
    collab_metrics = {item["id"]: item for item in components["collaboration"]["metrics"]}
    rag_metrics = {item["id"]: item for item in components["rag"]["metrics"]}
    provider_metrics = {item["id"]: item for item in components["providers"]["metrics"]}

    assert res.status_code == 200
    assert data["room_id"] == "main"
    assert data["status"] in {"healthy", "needs_attention", "blocked"}
    assert {"collaboration", "agents", "rag", "providers"}.issubset(components)
    assert collab_metrics["messages"]["value"] >= 1
    assert collab_metrics["memory_items"]["value"] >= 1
    assert rag_metrics["documents"]["value"] == 0
    assert provider_metrics["external_credentials"]["value"] == 0
    assert data["warnings"]


def test_observability_snapshot_requires_room_membership(client):
    member = _token(client)
    outsider = _outsider_token(client)
    assert client.post(
        "/collab/rooms/main/join",
        headers={"Authorization": f"Bearer {member}"},
        json={"room_name": "Ops room", "project": "demo"},
    ).status_code == 200

    res = client.get(
        "/system/observability?room_id=main",
        headers={"Authorization": f"Bearer {outsider}"},
    )

    assert res.status_code == 403
    assert "Join this room" in res.json()["detail"]


def test_event_store_readiness_api_reports_migration_status(client):
    res = client.get(
        "/system/event-store/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["backend"] == "json"
    assert data["status"] == "migration_available"
    assert data["migration_available"] is True
    assert "migrate_collab_json_to_sqlite.py" in data["migration_command"]
    assert any(check["id"] == "migration_script" for check in data["checks"])


def test_security_readiness_blocks_unsafe_production_defaults(client):
    settings = app.dependency_overrides[get_settings]().model_copy(
        update={
            "deployment_environment": "production",
            "jwt_secret": "change-me-in-production-voiceops",
            "cors_allowed_origins": ["*"],
            "collab_store_backend": "json",
            "speaker_store_backend": "json",
            "speaker_provider": "mock",
            "voiceops_workspace": None,
            "seed_demo_users": True,
        }
    )

    report = build_production_security_report(settings)
    checks = {check.id: check for check in report.checks}

    assert report.ready is False
    assert report.status == "needs_attention"
    assert checks["jwt_secret"].ready is False
    assert checks["cors_origins"].ready is False
    assert checks["default_users"].ready is False
    assert checks["default_users"].evidence["seed_demo_users"] is True
    assert checks["collab_store"].ready is False
    assert checks["speaker_store"].ready is False
    assert checks["workspace_boundary"].ready is False
    assert checks["speaker_provider"].ready is False
    assert checks["external_agent_credentials"].ready is False
    assert checks["external_agent_execution_policy"].ready is False
    assert checks["external_agent_execution_policy"].evidence["oauth_mock_enabled"] is True
    assert checks["github_side_effects"].ready is True
    assert any(threat.category == "Spoofing" for threat in report.threats)
    assert any(req.id == "SR-CHANGE-01" for req in report.requirements)


def test_security_readiness_accepts_hardened_production_config(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "id": "user-prod-admin",
                        "email": "ops@example.com",
                        "name": "Ops Admin",
                        "initials": "OA",
                        "role": "admin",
                        "password_hash": "$2b$12$abcdefghijklmnopqrstuu7nohashplaceholder123456789",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    llm_connections = runtime_dir / "llm_connections.json"
    external_credentials = runtime_dir / "external_agent_credentials.json"
    llm_connections.write_text("[]", encoding="utf-8")
    external_credentials.write_text("[]", encoding="utf-8")
    llm_connections.chmod(0o600)
    external_credentials.chmod(0o600)
    settings = Settings(
        deployment_environment="production",
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
        external_agent_oauth_mock_enabled=False,
        github_pr_creation_enabled=False,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=llm_connections,
        external_agent_store_path=external_credentials,
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    runtime_paths = next(check for check in report.checks if check.id == "runtime_artifact_paths")

    assert report.ready is True
    assert report.status == "ready"
    assert report.next_steps == []
    assert all(check.ready for check in report.checks)
    assert runtime_paths.ready is True
    assert runtime_paths.evidence["artifacts"]["agent_runs"]["in_workspace"] is False
    assert runtime_paths.evidence["artifacts"]["llm_connections"]["private_file"] is True
    assert runtime_paths.evidence["artifacts"]["external_agent_credentials"]["private_file"] is True
    validate_startup_security(settings)


def test_security_readiness_rejects_change_me_placeholder_secrets(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    settings = Settings(
        deployment_environment="production",
        jwt_secret="x" * 40,
        cors_allowed_origins=["https://voiceops.example.com"],
        collab_store_backend="sqlite",
        speaker_store_backend="sqlite",
        speaker_provider="whisperx",
        hf_token="change-me-huggingface-token",
        voiceops_workspace=str(workspace),
        users_store_path=users_path,
        seed_demo_users=False,
        external_agent_credential_secret="change-me-external-agent-credential-secret",
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

    checks = {check.id: check for check in build_production_security_report(settings).checks}

    assert checks["speaker_provider"].ready is False
    assert checks["speaker_provider"].evidence["hf_token_configured"] is False
    assert checks["external_agent_credentials"].ready is False
    assert checks["external_agent_credentials"].evidence["secret_configured"] is False


def test_security_readiness_blocks_runtime_artifacts_inside_workspace(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    gh.chmod(0o755)
    settings = Settings(
        deployment_environment="production",
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
        github_pr_creation_enabled=False,
        agent_runs_path=workspace / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    runtime_paths = next(check for check in report.checks if check.id == "runtime_artifact_paths")

    assert report.ready is False
    assert runtime_paths.ready is False
    assert runtime_paths.evidence["artifacts"]["agent_runs"]["in_workspace"] is True
    assert "inside VOICEOPS_WORKSPACE" in runtime_paths.detail
    with pytest.raises(StartupSecurityError, match="runtime_artifact_paths"):
        validate_startup_security(settings)


def test_security_readiness_blocks_world_readable_sensitive_runtime_artifacts(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    external_credentials = runtime_dir / "external_agent_credentials.json"
    rag_index = runtime_dir / "rag_index.json"
    long_memory = runtime_dir / "long_memory.json"
    external_credentials.write_text("[]", encoding="utf-8")
    rag_index.write_text('{"documents":[]}', encoding="utf-8")
    long_memory.write_text('{"records":[]}', encoding="utf-8")
    external_credentials.chmod(0o644)
    rag_index.chmod(0o644)
    long_memory.chmod(0o644)
    settings = Settings(
        deployment_environment="production",
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
        github_pr_creation_enabled=False,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=long_memory,
        rag_index_path=rag_index,
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=external_credentials,
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    runtime_paths = next(check for check in report.checks if check.id == "runtime_artifact_paths")

    assert report.ready is False
    assert runtime_paths.ready is False
    assert runtime_paths.evidence["artifacts"]["external_agent_credentials"]["mode"] == "0o644"
    assert runtime_paths.evidence["artifacts"]["external_agent_credentials"]["private_file"] is False
    assert runtime_paths.evidence["artifacts"]["rag_index"]["private_file"] is False
    assert runtime_paths.evidence["artifacts"]["long_memory"]["private_file"] is False
    assert "owner-readable only" in runtime_paths.detail


def test_security_readiness_blocks_world_readable_user_store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = runtime_dir / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o644)
    settings = Settings(
        deployment_environment="production",
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
        github_pr_creation_enabled=False,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    runtime_paths = next(check for check in report.checks if check.id == "runtime_artifact_paths")

    assert report.ready is False
    assert runtime_paths.ready is False
    assert runtime_paths.evidence["artifacts"]["users"]["mode"] == "0o644"
    assert runtime_paths.evidence["artifacts"]["users"]["private_file"] is False
    assert "users must be owner-readable only" in runtime_paths.detail


def test_security_readiness_local_runtime_artifacts_inside_workspace_are_labeled_as_local_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(
        deployment_environment="local",
        voiceops_workspace=str(workspace),
        agent_runs_path=workspace / "agent_runs.json",
        agent_llm_routes_path=workspace / "agent_llm_routes.json",
        long_memory_path=workspace / "long_memory.json",
        rag_index_path=workspace / "rag_index.json",
        llm_connection_store_path=workspace / "llm_connections.json",
        external_agent_store_path=workspace / "external_agent_credentials.json",
        demo_evidence_path=workspace / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    runtime_paths = next(check for check in report.checks if check.id == "runtime_artifact_paths")

    assert runtime_paths.ready is True
    assert runtime_paths.evidence["artifacts"]["agent_runs"]["in_workspace"] is True
    assert "Local mode permits" in runtime_paths.detail
    assert "production blockers" in runtime_paths.detail


def test_startup_security_gate_blocks_critical_production_misconfig(tmp_path):
    settings = Settings(
        deployment_environment="production",
        jwt_secret="change-me-in-production-voiceops",
        cors_allowed_origins=["https://voiceops.example.com"],
        collab_store_backend="sqlite",
        speaker_store_backend="sqlite",
        speaker_provider="whisperx",
        hf_token="hf_test_token",
        voiceops_workspace=str(tmp_path),
        users_store_path=tmp_path / "users.json",
        seed_demo_users=True,
        external_agent_credential_secret="y" * 40,
    )

    with pytest.raises(StartupSecurityError, match="jwt_secret"):
        validate_startup_security(settings)


def test_security_readiness_accepts_github_pr_creation_with_controls(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    gh.chmod(0o755)
    settings = Settings(
        deployment_environment="production",
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
        external_agent_oauth_mock_enabled=False,
        github_pr_creation_enabled=True,
        github_pr_cli_path=str(gh),
        github_pr_allowed_base_branches=["main", "release"],
        github_pr_command_timeout_seconds=30,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    github = next(check for check in report.checks if check.id == "github_side_effects")

    assert report.ready is True
    assert github.ready is True
    assert github.evidence["github_pr_creation_enabled"] is True
    assert github.evidence["allowed_base_branches"] == ["main", "release"]
    assert github.evidence["cli_available"] is True
    assert github.evidence["cli_authenticated"] is True


def test_security_readiness_blocks_github_pr_creation_without_controls(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(
        deployment_environment="production",
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
        github_pr_creation_enabled=True,
        github_pr_allowed_base_branches=[],
    )

    report = build_production_security_report(settings)
    github = next(check for check in report.checks if check.id == "github_side_effects")

    assert report.ready is False
    assert github.ready is False
    assert "without required branch allowlist" in github.detail


def test_security_readiness_blocks_github_pr_creation_without_cli(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    settings = Settings(
        deployment_environment="production",
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
        github_pr_creation_enabled=True,
        github_pr_cli_path=str(tmp_path / "missing-gh"),
        github_pr_allowed_base_branches=["main"],
        github_pr_command_timeout_seconds=30,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    github = next(check for check in report.checks if check.id == "github_side_effects")

    assert report.ready is False
    assert github.ready is False
    assert github.evidence["cli_available"] is False
    assert github.evidence["cli_authenticated"] is False
    assert "available GitHub CLI" in github.detail


def test_security_readiness_blocks_github_pr_creation_without_cli_auth(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    gh.chmod(0o755)
    settings = Settings(
        deployment_environment="production",
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
        github_pr_creation_enabled=True,
        github_pr_cli_path=str(gh),
        github_pr_allowed_base_branches=["main"],
        github_pr_command_timeout_seconds=30,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    github = next(check for check in report.checks if check.id == "github_side_effects")

    assert report.ready is False
    assert github.ready is False
    assert github.evidence["cli_available"] is True
    assert github.evidence["cli_authenticated"] is False
    assert "GitHub CLI authentication" in github.detail


def test_security_readiness_blocks_unsafe_external_agent_execution_policy(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    settings = Settings(
        deployment_environment="production",
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
        external_agent_oauth_mock_enabled=False,
        external_agent_allowed_providers=["codex", "unknown-agent"],
        external_agent_api_execution_enabled=True,
        openai_api_base_url="http://openai.example.test/v1",
        external_agent_cli_timeout_seconds=500,
        agent_runs_path=runtime_dir / "agent_runs.json",
        agent_llm_routes_path=runtime_dir / "agent_llm_routes.json",
        long_memory_path=runtime_dir / "long_memory.json",
        rag_index_path=runtime_dir / "rag_index.json",
        llm_connection_store_path=runtime_dir / "llm_connections.json",
        external_agent_store_path=runtime_dir / "external_agent_credentials.json",
        demo_evidence_path=runtime_dir / "demo_evidence.json",
    )

    report = build_production_security_report(settings)
    policy = next(check for check in report.checks if check.id == "external_agent_execution_policy")

    assert report.ready is False
    assert policy.ready is False
    assert policy.evidence["unknown_providers"] == ["unknown-agent"]
    assert policy.evidence["oauth_mock_enabled"] is False
    assert policy.evidence["api_base_urls_https"]["openai"] is False
    assert "unknown providers configured" in policy.detail
    assert "non-HTTPS base URLs" in policy.detail
    assert "CLI timeout" in policy.detail
    with pytest.raises(StartupSecurityError, match="external_agent_execution_policy"):
        validate_startup_security(settings)


def test_security_readiness_api_requires_admin_permission(client):
    on_call = client.get(
        "/system/security/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    admin = client.get(
        "/system/security/readiness",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )

    assert on_call.status_code == 403
    assert admin.status_code == 200
    assert "checks" in admin.json()


def test_production_cutover_api_requires_admin_permission(client):
    on_call = client.get(
        "/system/deployment/cutover",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    admin = client.get(
        "/system/deployment/cutover",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )

    assert on_call.status_code == 403
    assert admin.status_code == 200
    body = admin.json()
    assert "checks" in body
    assert body["env_file"] is None


def test_operator_acceptance_api_reports_dashboard_summary(client, monkeypatch):
    def fake_acceptance(settings, run_harnesses):
        assert settings is app.dependency_overrides[get_settings]()
        assert run_harnesses is False
        return {
            "status": "needs_attention",
            "accepted": False,
            "env_file": None,
            "run_harnesses": run_harnesses,
            "run_profile": None,
            "duration_ms": 42,
            "stages": [
                {
                    "id": "team_onboarding",
                    "label": "Team onboarding package",
                    "status": "ready",
                    "ready": True,
                    "required": True,
                    "duration_ms": 10,
                    "summary": "onboarding ready",
                    "next_steps": [],
                    "evidence": ["operator_acceptance_flow"],
                },
                {
                    "id": "final_acceptance",
                    "label": "Final multi-person AI teammate acceptance",
                    "status": "needs_attention",
                    "ready": False,
                    "required": True,
                    "duration_ms": 32,
                    "summary": "strict harness evidence missing",
                    "next_steps": ["Run operator acceptance with --require-accepted."],
                    "evidence": [],
                },
            ],
            "next_steps": ["Run operator acceptance with --require-accepted."],
        }

    monkeypatch.setattr(system_router, "_run_operator_acceptance_report", fake_acceptance)

    res = client.get(
        "/system/operator-acceptance",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["status"] == "needs_attention"
    assert data["accepted"] is False
    assert data["run_harnesses"] is False
    assert data["stages"][0]["id"] == "team_onboarding"
    assert data["stages"][1]["ready"] is False
    assert data["next_steps"] == ["Run operator acceptance with --require-accepted."]
    assert data["checked_at"]


def test_operator_acceptance_harness_run_requires_admin(client, monkeypatch):
    captured = {}

    def fake_acceptance(_settings, run_harnesses):
        captured["run_harnesses"] = run_harnesses
        return {
            "status": "ready",
            "accepted": True,
            "env_file": None,
            "run_harnesses": run_harnesses,
            "run_profile": None,
            "duration_ms": 1,
            "stages": [],
            "next_steps": [],
        }

    monkeypatch.setattr(system_router, "_run_operator_acceptance_report", fake_acceptance)

    on_call = client.get(
        "/system/operator-acceptance?run_harnesses=true",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    admin = client.get(
        "/system/operator-acceptance?run_harnesses=true",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )

    assert on_call.status_code == 403
    assert admin.status_code == 200
    assert admin.json()["run_harnesses"] is True
    assert captured == {"run_harnesses": True}


def test_runtime_status_reports_whisperx_missing_dependencies(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"

    def fake_find_spec(module_name):
        return object() if module_name == "torch" else None

    monkeypatch.setattr(system_router.importlib.util, "find_spec", fake_find_spec)

    res = client.get(
        "/system/runtime/status",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    speaker = next(item for item in data["providers"] if item["id"] == "speaker")
    checks = {item["id"]: item for item in speaker["checks"]}

    assert res.status_code == 200
    assert speaker["ready"] is False
    assert "WhisperX package" in speaker["detail"]
    assert checks["hf_token"]["ready"] is True
    assert checks["whisperx"]["ready"] is False
    assert checks["torch"]["ready"] is True
    assert checks["pyannote"]["ready"] is False
    assert any("WhisperX speaker provider is not ready" in warning for warning in data["warnings"])


def test_runtime_status_reports_whisperx_cpu_lag_warning(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_device = "cpu"

    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.get(
        "/system/runtime/status",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    speaker = next(item for item in data["providers"] if item["id"] == "speaker")

    assert res.status_code == 200
    assert speaker["ready"] is True
    assert "CPU mode may lag" in speaker["detail"]
    assert all(item["ready"] for item in speaker["checks"])
    assert any("CPU mode" in warning for warning in data["warnings"])


def test_runtime_status_reports_whisperx_in_process_worker_mode(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_device = "cpu"
    settings.whisperx_worker_mode = "in_process"

    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.get(
        "/system/runtime/status",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    speaker = next(item for item in data["providers"] if item["id"] == "speaker")

    assert res.status_code == 200
    assert speaker["ready"] is True
    assert "worker mode in_process" in speaker["detail"]
    assert any("in-process worker" in warning for warning in data["warnings"])


def test_runtime_status_reports_whisperx_persistent_subprocess_worker_mode(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_device = "cpu"
    settings.whisperx_worker_mode = "persistent_subprocess"

    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.get(
        "/system/runtime/status",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    speaker = next(item for item in data["providers"] if item["id"] == "speaker")

    assert res.status_code == 200
    assert speaker["ready"] is True
    assert "worker mode persistent_subprocess" in speaker["detail"]
    assert any("persistent subprocess" in warning for warning in data["warnings"])


def test_local_doctor_reports_blocking_whisperx_prerequisites(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "mock"
    settings.hf_token = None
    settings.whisperx_device = "cpu"

    monkeypatch.setattr(system_router.shutil, "which", lambda _name: None)
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: None)

    res = client.get(
        "/system/local-doctor",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert data["ready"] is False
    assert data["status"] == "needs_attention"
    assert checks["speaker_provider"]["ready"] is False
    assert checks["hf_token"]["ready"] is False
    assert checks["ffmpeg"]["ready"] is False
    assert checks["whisperx"]["ready"] is False
    assert checks["speaker_hints"]["status"] == "warning"
    assert any("SPEAKER_PROVIDER=whisperx" in step for step in data["next_steps"])
    assert any("brew install ffmpeg" in step for step in data["next_steps"])
    assert data["command"].endswith("--require-real-diarization")


def test_local_doctor_reports_ready_with_cpu_warnings(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_device = "cpu"
    settings.whisperx_min_speakers = 2
    settings.whisperx_max_speakers = 2

    monkeypatch.setattr(system_router.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.get(
        "/system/local-doctor",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert data["ready"] is True
    assert data["status"] == "ready"
    assert checks["speaker_provider"]["ready"] is True
    assert checks["hf_token"]["ready"] is True
    assert checks["ffmpeg"]["detail"] == "/opt/homebrew/bin/ffmpeg"
    assert checks["device"]["status"] == "warning"
    assert checks["speaker_hints"]["status"] == "ready"
    assert any("CPU mode" in warning for warning in data["warnings"])


def test_target_readiness_does_not_treat_mock_state_as_complete(client):
    res = client.get(
        "/system/target-readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    milestones = {item["id"]: item for item in data["milestones"]}

    assert res.status_code == 200
    assert data["ready"] is False
    assert data["status"] == "needs_attention"
    assert milestones["workspace_git"]["ready"] is False
    assert "bootstrap_local_runtime.py" in milestones["workspace_git"]["command"]
    assert milestones["ai_memory_loop"]["ready"] is False
    assert milestones["durable_event_store"]["ready"] is False
    assert milestones["durable_event_store"]["status"] == "migration_available"
    assert "bootstrap_local_runtime.py" in milestones["durable_event_store"]["command"]
    assert milestones["real_multi_speaker"]["ready"] is False
    assert milestones["real_browser_live"]["ready"] is False
    assert any("bootstrap" in step.lower() for step in data["next_steps"])
    assert any("mock closure gate" in step.lower() for step in data["next_steps"])


def test_target_readiness_reports_complete_when_all_evidence_exists(client, tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    (workspace / "app.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")

    settings = app.dependency_overrides[get_settings]()
    settings.voiceops_workspace = str(workspace)
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_device = "cpu"
    settings.collab_store_backend = "sqlite"
    settings.collab_sqlite_path = tmp_path / "collab.sqlite3"
    sqlite_service = CollaborationService(SQLiteCollaborationStore(settings.collab_sqlite_path))
    sqlite_user = UserPublic(
        id="usr_readiness",
        email="readiness@voiceops.dev",
        name="Readiness User",
        initials="RU",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
    sqlite_service.join_room("main", sqlite_user, JoinRoomRequest(room_name="Readiness room"))
    sqlite_service.add_user_message("main", sqlite_user, TextMessageRequest(text="Durable event log is ready."))
    settings.speaker_verification_path.write_text(
        json.dumps(
            {
                "checked_at": "2026-06-18T12:00:00+00:00",
                "provider": "whisperx",
                "verified": True,
                "strict_multi_speaker": True,
                "distinct_speaker_count": 2,
                "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                "detail": "Verified with 2 speaker labels",
            }
        ),
        encoding="utf-8",
    )
    settings.demo_evidence_path.write_text(
        json.dumps(
            {
                "checked_at": "2026-06-18T12:05:00+00:00",
                "records": [
                    {
                        "id": "mock_e2e",
                        "label": "Mock closure harness",
                        "status": "passed",
                        "checked_at": "2026-06-18T12:01:00+00:00",
                        "detail": "Closure harness passed",
                    },
                    {
                        "id": "real_live_backend",
                        "label": "Real WhisperX backend live",
                        "status": "passed",
                        "checked_at": "2026-06-18T12:02:00+00:00",
                        "detail": "Real live backend passed",
                        "distinct_speaker_count": 2,
                        "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                        "requested_chunks": 2,
                        "completed_chunks": 2,
                        "timeline_message_count": 4,
                    },
                    {
                        "id": "real_browser_live",
                        "label": "Real browser mic live",
                        "status": "passed",
                        "checked_at": "2026-06-18T12:03:00+00:00",
                        "detail": "Browser mic live passed",
                        "distinct_speaker_count": 2,
                        "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                        "requested_chunks": 1,
                        "completed_chunks": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.get(
        "/system/target-readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    milestones = {item["id"]: item for item in data["milestones"]}

    assert res.status_code == 200
    assert data["ready"] is True
    assert data["score"] == 100
    assert data["next_steps"] == []
    assert all(item["ready"] for item in data["milestones"])
    assert milestones["durable_event_store"]["evidence"].startswith("sqlite |")


def test_target_readiness_rejects_weak_real_live_evidence(client, tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    (workspace / "app.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")

    settings = app.dependency_overrides[get_settings]()
    settings.voiceops_workspace = str(workspace)
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_device = "cpu"
    settings.speaker_verification_path.write_text(
        json.dumps(
            {
                "checked_at": "2026-06-18T12:00:00+00:00",
                "provider": "whisperx",
                "verified": True,
                "strict_multi_speaker": True,
                "distinct_speaker_count": 2,
                "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                "detail": "Verified with 2 speaker labels",
            }
        ),
        encoding="utf-8",
    )
    settings.demo_evidence_path.write_text(
        json.dumps(
            {
                "checked_at": "2026-06-18T12:05:00+00:00",
                "records": [
                    {
                        "id": "mock_e2e",
                        "label": "Mock closure harness",
                        "status": "passed",
                        "checked_at": "2026-06-18T12:01:00+00:00",
                        "detail": "Closure harness passed",
                    },
                    {
                        "id": "real_live_backend",
                        "label": "Real WhisperX backend live",
                        "status": "passed",
                        "checked_at": "2026-06-18T12:02:00+00:00",
                        "detail": "Weak backend live evidence should not pass target readiness",
                        "distinct_speaker_count": 1,
                        "speaker_labels": ["SPEAKER_00"],
                        "requested_chunks": 2,
                        "completed_chunks": 2,
                        "timeline_message_count": 4,
                    },
                    {
                        "id": "real_browser_live",
                        "label": "Real browser mic live",
                        "status": "passed",
                        "checked_at": "2026-06-18T12:03:00+00:00",
                        "detail": "Browser mic live passed",
                        "distinct_speaker_count": 2,
                        "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                        "requested_chunks": 1,
                        "completed_chunks": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.get(
        "/system/target-readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    milestones = {item["id"]: item for item in data["milestones"]}

    assert res.status_code == 200
    assert data["ready"] is False
    assert milestones["real_live_backend"]["ready"] is False
    assert milestones["real_live_backend"]["status"] == "unproven"
    assert milestones["real_multi_speaker"]["ready"] is True


def test_system_readiness_reports_disconnected_workspace(client):
    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert data["status"] == "needs_attention"
    assert data["ready"] is False
    assert data["demo_command"] == "cd backend && python scripts/demo_readiness.py"
    assert checks["backend"]["ready"] is True
    assert checks["workspace"]["ready"] is False
    assert checks["git"]["ready"] is False
    assert checks["speaker"]["ready"] is True
    assert checks["diarization"]["ready"] is False
    assert checks["diarization"]["status"] == "not_verified"
    assert data["git"] is None
    assert data["speaker_verification"]["exists"] is False
    assert data["demo_evidence"]["exists"] is False
    assert data["demo_evidence"]["status"] == "not_recorded"


def test_system_readiness_reports_connected_git_workspace(client, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "VoiceOps Test"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, capture_output=True)

    settings = app.dependency_overrides[get_settings]()
    settings.voiceops_workspace = str(workspace)

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert data["status"] == "ready"
    assert data["ready"] is True
    assert checks["workspace"]["ready"] is True
    assert checks["git"]["ready"] is True
    assert checks["memory"]["ready"] is True
    assert checks["live"]["ready"] is True
    assert checks["diarization"]["ready"] is False
    assert data["git"]["is_git_repo"] is True
    assert data["git"]["dirty"] is False


def test_system_readiness_reports_verified_real_diarization(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_verification_path = tmp_path / "speaker_verification.json"
    settings.speaker_verification_path.write_text(
        (
            "{"
            '"checked_at":"2026-06-18T12:00:00+00:00",'
            '"provider":"whisperx",'
            '"verified":true,'
            '"distinct_speaker_count":2,'
            '"speaker_labels":["SPEAKER_00","SPEAKER_01"],'
            '"elapsed_ms":1234,'
            '"strict_multi_speaker":true,'
            '"generated_audio":false,'
            '"quality":{"level":"verified","segment_count":2,"has_transcript":true,"multi_speaker":true,"strict_passed":true,"notes":[]},'
            '"detail":"Verified 2 speaker label(s) with WhisperX"'
            "}"
        ),
        encoding="utf-8",
    )

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert checks["diarization"]["ready"] is True
    assert checks["diarization"]["status"] == "verified"
    assert data["speaker_verification"]["verified"] is True
    assert data["speaker_verification"]["distinct_speaker_count"] == 2
    assert data["speaker_verification"]["speaker_labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert data["speaker_verification"]["quality"]["level"] == "verified"
    assert data["speaker_verification"]["quality"]["strict_passed"] is True


def test_system_readiness_marks_single_speaker_verification_as_weak(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_verification_path = tmp_path / "speaker_verification.json"
    settings.speaker_verification_path.write_text(
        (
            "{"
            '"checked_at":"2026-06-18T12:00:00+00:00",'
            '"provider":"whisperx",'
            '"verified":true,'
            '"distinct_speaker_count":1,'
            '"speaker_labels":["SPEAKER_00"],'
            '"elapsed_ms":1234,'
            '"strict_multi_speaker":false,'
            '"quality":{"level":"weak","segment_count":1,"has_transcript":true,"multi_speaker":false,"strict_passed":false},'
            '"detail":"Verified 1 speaker label with WhisperX"'
            "}"
        ),
        encoding="utf-8",
    )

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert data["speaker_verification"]["verified"] is True
    assert data["speaker_verification"]["distinct_speaker_count"] == 1
    assert checks["diarization"]["ready"] is False
    assert checks["diarization"]["status"] == "weak"
    assert "strict 2+ speaker verification" in checks["diarization"]["detail"]


def test_system_readiness_reports_invalid_real_diarization_file(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_verification_path = tmp_path / "speaker_verification.json"
    settings.speaker_verification_path.write_text("{not-json", encoding="utf-8")

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    checks = {item["id"]: item for item in data["checks"]}

    assert res.status_code == 200
    assert data["ready"] is False
    assert checks["diarization"]["ready"] is False
    assert checks["diarization"]["status"] == "invalid"
    assert data["speaker_verification"]["exists"] is True
    assert data["speaker_verification"]["error"]


def test_system_readiness_reports_failed_real_diarization_stage(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_verification_path = tmp_path / "speaker_verification.json"
    settings.speaker_verification_path.write_text(
        (
            "{"
            '"checked_at":"2026-06-18T12:00:00+00:00",'
            '"provider":"whisperx",'
            '"verified":false,'
            '"error":"WhisperX worker timed out",'
            '"elapsed_ms":600493,'
            '"last_stage":"diarizing",'
            '"stages":[{"stage":"diarizing","message":"Loading pyannote diarization pipeline","elapsed_ms":10279}],'
            '"warnings":["CPU diarization can exceed the smoke timeout."],'
            '"config":{"whisperx_model":"tiny","whisperx_device":"cpu"},'
            '"detail":"WhisperX worker timed out"'
            "}"
        ),
        encoding="utf-8",
    )

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["speaker_verification"]["status"] == "failed"
    assert data["speaker_verification"]["last_stage"] == "diarizing"
    assert data["speaker_verification"]["warnings"] == ["CPU diarization can exceed the smoke timeout."]
    assert data["speaker_verification"]["config"]["whisperx_model"] == "tiny"


def test_system_readiness_reports_persisted_demo_evidence(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.demo_evidence_path = tmp_path / "demo_evidence.json"
    settings.demo_evidence_path.write_text(
        json.dumps(
            {
                "checked_at": "2026-06-18T13:10:00+00:00",
                "records": [
                    {
                        "id": "real_live_backend",
                        "label": "Real WhisperX backend live",
                        "status": "passed",
                        "checked_at": "2026-06-18T13:00:00+00:00",
                        "source": "backend_smoke_live_meeting_real",
                        "provider": "whisperx",
                        "detail": "Real live WebSocket smoke passed",
                        "duration_ms": 7100,
                        "command": ["python", "scripts/smoke_live_meeting_real.py", "--json"],
                        "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                        "distinct_speaker_count": 2,
                        "requested_chunks": 2,
                        "completed_chunks": 2,
                        "timeline_message_count": 4,
                        "latency": {"cold_start_ms": 6200, "best_warm_ms": 980},
                    },
                    {
                        "id": "real_browser_live",
                        "label": "Real browser mic live",
                        "status": "failed",
                        "checked_at": "2026-06-18T13:05:00+00:00",
                        "source": "frontend_live_meeting_real_mic_e2e",
                        "provider": "whisperx",
                        "error": "missing real speaker label SPEAKER_01",
                        "detail": "missing real speaker label SPEAKER_01",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()
    records = {item["id"]: item for item in data["demo_evidence"]["records"]}

    assert res.status_code == 200
    assert data["demo_evidence"]["exists"] is True
    assert data["demo_evidence"]["status"] == "recorded"
    assert data["demo_evidence"]["checked_at"] == "2026-06-18T13:10:00+00:00"
    assert records["real_live_backend"]["status"] == "passed"
    assert records["real_live_backend"]["completed_chunks"] == 2
    assert records["real_live_backend"]["speaker_labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert records["real_browser_live"]["status"] == "failed"
    assert records["real_browser_live"]["error"] == "missing real speaker label SPEAKER_01"


def test_system_readiness_reports_invalid_demo_evidence_file(client, tmp_path):
    settings = app.dependency_overrides[get_settings]()
    settings.demo_evidence_path = tmp_path / "demo_evidence.json"
    settings.demo_evidence_path.write_text("{not-json", encoding="utf-8")

    res = client.get(
        "/system/readiness",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["demo_evidence"]["exists"] is True
    assert data["demo_evidence"]["status"] == "invalid"
    assert data["demo_evidence"]["error"]


def test_demo_gate_run_starts_background_job_and_records_evidence(client, monkeypatch, tmp_path):
    system_router._demo_gate_run_job = None
    system_router._demo_gate_run_task = None
    system_router._demo_gate_completion_targets.clear()
    settings = app.dependency_overrides[get_settings]()
    settings.demo_evidence_path = tmp_path / "demo_evidence.json"
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.returncode = 0
            self.stdout = system_router.asyncio.StreamReader()
            self.stderr = system_router.asyncio.StreamReader()
            self.stdout.feed_data(b'{"status":"ready","checks":[]}\n')
            self.stdout.feed_eof()
            self.stderr.feed_data(b"real mic progress: provider=whisperx chunks=1 messages=1 labels=SPEAKER_00\n")
            self.stderr.feed_eof()


        async def wait(self):
            return 0

        def kill(self):
            return None

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        system_router.write_demo_evidence_record(
            settings.demo_evidence_path,
            {
                "id": "mock_e2e",
                "label": "Mock closure harness",
                "status": "passed",
                "source": "test",
                "provider": "mock",
                "detail": "test passed",
                "command": list(args),
                "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                "distinct_speaker_count": 2,
                "requested_chunks": 3,
                "completed_chunks": 3,
            },
        )
        return FakeProcess()

    monkeypatch.setattr(system_router.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    token = _admin_token(client)

    started = client.post("/system/demo/gates/mock_e2e/runs", headers={"Authorization": f"Bearer {token}"})
    status = None
    for _ in range(20):
        status = client.get("/system/demo/gates/runs/current", headers={"Authorization": f"Bearer {token}"})
        if status.json()["state"] == "succeeded":
            break
        time.sleep(0.01)
    data = status.json()

    assert started.status_code == 200
    assert started.json()["state"] == "running"
    assert started.json()["gate_id"] == "mock_e2e"
    assert "--evidence-path" in captured["args"]
    assert captured["env"]["DEMO_EVIDENCE_PATH"] == str(settings.demo_evidence_path)
    assert data["state"] == "succeeded"
    stage_names = [stage["stage"] for stage in data["stages"]]
    assert "queued" in stage_names
    assert "running_process" in stage_names
    assert "browser_progress" in stage_names
    assert stage_names[-1] == "succeeded"
    assert data["evidence"]["records"][0]["id"] == "mock_e2e"
    assert data["evidence"]["records"][0]["status"] == "passed"


def test_demo_gate_run_rejects_unknown_and_concurrent_gate(client):
    system_router._demo_gate_run_job = system_router.DemoGateRunResponse(
        job_id="gate-running",
        gate_id="mock_e2e",
        label="Mock closure harness",
        state="running",
    )
    system_router._demo_gate_completion_targets.clear()
    token = _admin_token(client)

    unknown = client.post("/system/demo/gates/not-a-gate/runs", headers={"Authorization": f"Bearer {token}"})
    concurrent = client.post("/system/demo/gates/real_live_backend/runs", headers={"Authorization": f"Bearer {token}"})

    assert unknown.status_code == 404
    assert concurrent.status_code == 409


def test_speaker_warmup_reports_idle_and_skips_mock_provider(client):
    system_router._speaker_warmup_job = None
    system_router._speaker_warmup_task = None
    on_call = _token(client)
    token = _admin_token(client)

    idle = client.get("/system/speaker/warmup", headers={"Authorization": f"Bearer {on_call}"})
    forbidden = client.post("/system/speaker/warmup", headers={"Authorization": f"Bearer {on_call}"})
    started = client.post("/system/speaker/warmup", headers={"Authorization": f"Bearer {token}"})

    assert idle.status_code == 200
    assert idle.json()["state"] == "idle"
    assert forbidden.status_code == 403
    assert started.status_code == 200
    assert started.json()["state"] == "skipped"
    assert started.json()["ready"] is True


def test_speaker_warmup_fails_fast_when_whisperx_not_ready(client):
    system_router._speaker_warmup_job = None
    system_router._speaker_warmup_task = None
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = None

    res = client.post(
        "/system/speaker/warmup",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["state"] == "failed"
    assert data["ready"] is False
    assert "HF_TOKEN" in data["detail"]


def test_speaker_warmup_starts_background_job(client, monkeypatch):
    system_router._speaker_warmup_job = None
    system_router._speaker_warmup_task = None
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"

    async def fake_run(job_id, _settings):
        job = system_router._speaker_warmup_job
        assert job.job_id == job_id
        job.stages.append(system_router.SpeakerWarmupStage(stage="loading_asr", message="fake load", elapsed_ms=1))
        system_router._complete_warmup(job, state="succeeded", detail="fake warmup completed", ready=True)

    monkeypatch.setattr(system_router, "_run_speaker_warmup", fake_run)
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    token = _admin_token(client)
    started = client.post("/system/speaker/warmup", headers={"Authorization": f"Bearer {token}"})
    status = None
    for _ in range(20):
        status = client.get("/system/speaker/warmup", headers={"Authorization": f"Bearer {token}"})
        if status.json()["state"] == "succeeded":
            break
        time.sleep(0.01)

    assert started.status_code == 200
    assert started.json()["state"] == "running"
    assert status.json()["state"] == "succeeded"
    assert status.json()["ready"] is True
    assert status.json()["stages"][0]["stage"] == "loading_asr"


def test_speaker_warmup_in_process_reuses_live_provider(client, monkeypatch):
    system_router._speaker_warmup_job = None
    system_router._speaker_warmup_task = None
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.whisperx_worker_mode = "in_process"

    class FakeSpeakerService:
        async def warmup_provider(self, stage_callback=None):
            if stage_callback:
                stage_callback("loading_asr", "fake reusable ASR")
                stage_callback("loading_diarization", "fake reusable diarization")

    async def exploding_subprocess_warmup(*_args, **_kwargs):
        raise AssertionError("in-process warmup should not start the subprocess warmup path")

    app.dependency_overrides[get_speaker_service] = lambda: FakeSpeakerService()
    monkeypatch.setattr(system_router, "_run_speaker_warmup", exploding_subprocess_warmup)
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    token = _admin_token(client)
    started = client.post("/system/speaker/warmup", headers={"Authorization": f"Bearer {token}"})
    status = None
    for _ in range(20):
        status = client.get("/system/speaker/warmup", headers={"Authorization": f"Bearer {token}"})
        if status.json()["state"] == "succeeded":
            break
        time.sleep(0.01)

    assert started.status_code == 200
    assert started.json()["state"] == "running"
    data = status.json()
    assert data["state"] == "succeeded"
    assert data["ready"] is True
    assert "reused by live meetings" in data["detail"]
    assert [stage["stage"] for stage in data["stages"]] == ["loading_asr", "loading_diarization"]


def test_speaker_verification_reports_idle_state(client):
    system_router._speaker_verification_job = None
    system_router._speaker_verification_task = None

    res = client.get(
        "/system/speaker/verification",
        headers={"Authorization": f"Bearer {_token(client)}"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["state"] == "idle"
    assert data["verification"]["status"] == "not_verified"


def test_speaker_verification_requires_whisperx_provider(client):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "mock"

    res = client.post(
        "/system/speaker/verification",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        files={"audio": ("sample.wav", b"fake-audio", "audio/wav")},
    )

    assert res.status_code == 409
    assert "SPEAKER_PROVIDER=whisperx" in res.json()["detail"]


def test_speaker_verification_rejects_oversized_upload(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.speaker_verification_max_bytes = 4
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    res = client.post(
        "/system/speaker/verification",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
        files={"audio": ("sample.wav", b"fake-audio", "audio/wav")},
    )

    assert res.status_code == 413


def test_speaker_verification_starts_background_job_and_records_status(client, monkeypatch, tmp_path):
    system_router._speaker_verification_job = None
    system_router._speaker_verification_task = None
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.speaker_verification_path = tmp_path / "verification.json"
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    async def fake_run(job_id, audio_path, _settings, require_multiple_speakers):
        job = system_router._speaker_verification_job
        assert job.job_id == job_id
        assert audio_path.exists()
        assert require_multiple_speakers is True
        system_router._append_verification_stage(
            job,
            "running_whisperx",
            "WhisperX verification is running",
            started_at=job.started_at,
        )
        _settings.speaker_verification_path.write_text(
            json.dumps(
                {
                    "checked_at": "2026-06-18T12:00:00+00:00",
                    "provider": "whisperx",
                    "verified": True,
                    "strict_multi_speaker": True,
                    "distinct_speaker_count": 2,
                    "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                    "elapsed_ms": 42,
                    "detail": "Verified 2 speaker label(s) with WhisperX",
                }
            ),
            encoding="utf-8",
        )
        audio_path.unlink(missing_ok=True)
        system_router._append_verification_stage(
            job,
            "succeeded",
            "Real diarization verification passed",
            started_at=job.started_at,
        )
        system_router._complete_speaker_verification(
            job,
            state="succeeded",
            detail="fake verification completed",
            exit_code=0,
        )
        job.verification = system_router._speaker_verification_status(_settings)

    monkeypatch.setattr(system_router, "_run_speaker_verification", fake_run)

    on_call = _token(client)
    token = _admin_token(client)
    forbidden = client.post(
        "/system/speaker/verification",
        headers={"Authorization": f"Bearer {on_call}"},
        data={"require_multiple_speakers": "true"},
        files={"audio": ("sample.wav", b"fake-audio", "audio/wav")},
    )
    started = client.post(
        "/system/speaker/verification",
        headers={"Authorization": f"Bearer {token}"},
        data={"require_multiple_speakers": "true"},
        files={"audio": ("sample.wav", b"fake-audio", "audio/wav")},
    )
    status = None
    for _ in range(20):
        status = client.get("/system/speaker/verification", headers={"Authorization": f"Bearer {token}"})
        if status.json()["state"] == "succeeded":
            break
        time.sleep(0.01)

    assert forbidden.status_code == 403
    assert started.status_code == 200
    assert started.json()["state"] == "running"
    assert started.json()["poll_url"] == "/system/speaker/verification"
    assert started.json()["size_bytes"] == len(b"fake-audio")
    assert started.json()["timeout_seconds"] == settings.speaker_verification_timeout_seconds + 30
    assert started.json()["stages"][0]["stage"] == "accepted_upload"
    assert status.json()["state"] == "succeeded"
    assert [stage["stage"] for stage in status.json()["stages"]] == [
        "accepted_upload",
        "running_whisperx",
        "succeeded",
    ]
    assert status.json()["detail"] == "fake verification completed"
    assert status.json()["verification"]["verified"] is True
    assert status.json()["verification"]["speaker_labels"] == ["SPEAKER_00", "SPEAKER_01"]


def test_generated_speaker_verification_starts_background_job(client, monkeypatch, tmp_path):
    system_router._speaker_verification_job = None
    system_router._speaker_verification_task = None
    settings = app.dependency_overrides[get_settings]()
    settings.speaker_provider = "whisperx"
    settings.hf_token = "hf_test_token"
    settings.speaker_verification_path = tmp_path / "verification.json"
    monkeypatch.setattr(system_router.importlib.util, "find_spec", lambda _module_name: object())

    async def fake_run(job_id, audio_path, _settings, require_multiple_speakers):
        job = system_router._speaker_verification_job
        assert job.job_id == job_id
        assert audio_path is None
        assert require_multiple_speakers is True
        _settings.speaker_verification_path.write_text(
            json.dumps(
                {
                    "checked_at": "2026-06-18T12:00:00+00:00",
                    "provider": "whisperx",
                    "verified": True,
                    "strict_multi_speaker": True,
                    "generated_audio": True,
                    "distinct_speaker_count": 2,
                    "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                    "elapsed_ms": 42,
                    "quality": {
                        "level": "verified",
                        "segment_count": 2,
                        "has_transcript": True,
                        "multi_speaker": True,
                        "strict_passed": True,
                        "notes": [],
                    },
                    "detail": "Verified 2 speaker label(s) with WhisperX",
                }
            ),
            encoding="utf-8",
        )
        system_router._append_verification_stage(
            job,
            "succeeded",
            "Generated two-speaker verification passed",
            started_at=job.started_at,
        )
        system_router._complete_speaker_verification(
            job,
            state="succeeded",
            detail="generated verification completed",
            exit_code=0,
        )
        job.verification = system_router._speaker_verification_status(_settings)

    monkeypatch.setattr(system_router, "_run_speaker_verification", fake_run)

    token = _admin_token(client)
    started = client.post(
        "/system/speaker/verification/generated",
        headers={"Authorization": f"Bearer {token}"},
        data={"require_multiple_speakers": "true"},
    )
    status = None
    for _ in range(20):
        status = client.get("/system/speaker/verification", headers={"Authorization": f"Bearer {token}"})
        if status.json()["state"] == "succeeded":
            break
        time.sleep(0.01)

    assert started.status_code == 200
    assert started.json()["state"] == "running"
    assert started.json()["source"] == "generated_macos_tts"
    assert started.json()["generated_audio"] is True
    assert started.json()["filename"] == "generated-macos-two-speaker.wav"
    assert started.json()["stages"][0]["stage"] == "generating_audio"
    assert status.json()["state"] == "succeeded"
    assert status.json()["verification"]["generated_audio"] is True
    assert status.json()["verification"]["quality"]["level"] == "verified"


@pytest.mark.asyncio
async def test_generated_speaker_verification_runner_uses_generated_smoke_command(monkeypatch, tmp_path):
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_cache_path=tmp_path / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        speaker_provider="whisperx",
        hf_token="hf_test_token",
        speaker_verification_path=tmp_path / "speaker_verification.json",
        whisperx_worker_timeout_seconds=7,
        speaker_verification_timeout_seconds=240,
    )
    job = system_router._new_speaker_verification_job(
        settings,
        source="generated_macos_tts",
        detail="Generating macOS two-speaker sample; starting verification",
        filename="generated-macos-two-speaker.wav",
        size_bytes=0,
        require_multiple_speakers=True,
        generated_audio=True,
    )
    system_router._speaker_verification_job = job
    captured_args = []

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"{}", b""

        async def wait(self):
            return 0

        def kill(self):
            return None

    async def fake_create_subprocess_exec(*args, **_kwargs):
        captured_args.extend(args)
        return FakeProcess()

    monkeypatch.setattr(system_router.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await system_router._run_speaker_verification(
        job.job_id or "",
        None,
        settings,
        require_multiple_speakers=True,
    )

    assert job.state == "succeeded"
    assert "--generate-macos-tts" in captured_args
    assert "--require-multiple-speakers" in captured_args
    assert "--audio" not in captured_args
    assert captured_args[captured_args.index("--timeout") + 1] == "240.0"
    assert str(settings.speaker_verification_path) in captured_args


def test_react_build_serves_vite_assets(client):
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    index = dist / "index.html"
    if not index.exists():
        pytest.skip("React build output is not present")

    html = index.read_text(encoding="utf-8")
    asset_paths = [
        part.split('"', 1)[0]
        for part in html.split('href="')[1:] + html.split('src="')[1:]
        if part.startswith("/assets/")
    ]

    assert asset_paths
    assert client.get("/react").status_code == 200
    assert client.get(asset_paths[0]).status_code == 200
