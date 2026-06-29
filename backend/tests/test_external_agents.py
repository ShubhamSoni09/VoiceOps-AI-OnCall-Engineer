from __future__ import annotations

import subprocess
import textwrap
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.agents.service import MultiAgentService, get_multi_agent_service
from app.agents.store import AgentRunStore
from app.auth.users import UserStore
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
import app.external_agents.service as external_agent_service
import app.external_agents.adapters as external_agent_adapters
from app.external_agents.service import ExternalAgentService
from app.external_agents.store import ExternalAgentCredentialStore
import app.voice_agent.router as voice_router
from app.external_agents.adapters import ExternalAgentExecutionAdapter, ExternalCodingAgentAdapter
from app.external_agents.models import (
    ExternalAgentAuthMethod,
    ExternalAgentCredentialRecord,
    ExternalAgentProvider,
    ExternalAgentRunRequest,
)


def _write_workspace(path):
    path.mkdir()
    (path / "app.py").write_text(
        textwrap.dedent(
            """
            from fastapi import FastAPI

            app = FastAPI()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _init_git(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "VoiceOps Tests"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


def test_external_agent_store_repairs_existing_file_permissions(tmp_path):
    path = tmp_path / "external-agents.json"
    path.write_text("[]", encoding="utf-8")
    path.chmod(0o644)

    ExternalAgentCredentialStore(path)

    assert path.stat().st_mode & 0o777 == 0o600


def _token_for(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _token(client: TestClient) -> str:
    return _token_for(client, "priya@voiceops.dev", "oncall123")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _credential(provider: ExternalAgentProvider, *, command: str = "codex") -> tuple[ExternalAgentCredentialRecord, dict]:
    now = datetime.now(UTC)
    return (
        ExternalAgentCredentialRecord(
            id="xag-test",
            user_id="user-test",
            provider=provider,
            auth_method=ExternalAgentAuthMethod.LOCAL_CLI,
            account_label="Local CLI",
            encrypted_payload="unused",
            created_at=now,
            updated_at=now,
            metadata={},
        ),
        {"command": command},
    )


def test_external_read_only_local_cli_redacts_secret_like_output(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)

    class FakeRuntime:
        def __init__(self, _settings):
            pass

        def run_read_only_command(self, **_kwargs):
            return {
                "passed": True,
                "output": "reviewed with sk-test-secret-value-123456 and https://ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/team/repo.git",
            }

    monkeypatch.setattr(external_agent_adapters, "AgentRuntimeService", FakeRuntime)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-readonly-redaction-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    credential, payload = _credential(ExternalAgentProvider.CLAUDE, command="claude")

    result = ExternalAgentExecutionAdapter(settings).run_read_only(
        provider_run_id="xrun-readonly-redact",
        credential=credential,
        payload=payload,
        body=ExternalAgentRunRequest(provider=ExternalAgentProvider.CLAUDE, mode="review", prompt="review"),
        model="claude-sonnet-4-6",
    )
    serialized = f"{result.summary} {result.audit}"

    assert result.status == "completed"
    assert "sk-test-secret" not in serialized
    assert "ghp_" not in serialized
    assert "[redacted]" in serialized


class _FakeProviderResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_codex_cli_patch_adapter_runs_in_temp_workspace_and_returns_diff(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    calls = []

    def fake_runner(args, *, cwd, capture_output, text, timeout, shell, env):
        calls.append(
            {
                "args": args,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "shell": shell,
                "env": env,
            }
        )
        target = cwd / "app.py"
        target.write_text(
            target.read_text(encoding="utf-8")
            + '\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="patched", stderr="")

    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="codex-cli-patch-adapter-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    credential, payload = _credential(ExternalAgentProvider.CODEX, command="codex")
    body = ExternalAgentRunRequest(
        provider=ExternalAgentProvider.CODEX,
        mode="patch",
        model="gpt-5.4",
        prompt="fix health endpoint",
    )

    result = ExternalCodingAgentAdapter(settings, runner=fake_runner).propose_patch(
        provider_run_id="xrun-test",
        credential=credential,
        payload=payload,
        body=body,
        model="gpt-5.4",
    )

    assert result.status == "completed"
    assert result.files_changed == ["app.py"]
    assert "--- a/app.py" in result.diff
    assert "@app.get(\"/health\")" in result.proposed_files["app.py"]
    assert (workspace / "app.py").read_text(encoding="utf-8") == original
    assert calls[0]["cwd"] != workspace
    assert calls[0]["args"][:6] == ["codex", "exec", "--json", "--sandbox", "workspace-write", "--cd"]
    assert calls[0]["args"][-1] == "fix health endpoint"
    assert calls[0]["shell"] is False
    assert calls[0]["env"]["VOICEOPS_WORKSPACE"] == str(calls[0]["cwd"])
    assert result.audit["runtime"] == "isolated_temp_workspace"
    assert result.audit["workspace"] == "temporary"


def test_codex_cli_patch_adapter_reports_missing_cli_without_mutating_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")

    def missing_runner(*args, **kwargs):
        raise FileNotFoundError("codex")

    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="codex-cli-missing-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    credential, payload = _credential(ExternalAgentProvider.CODEX, command="codex")

    result = ExternalCodingAgentAdapter(settings, runner=missing_runner).propose_patch(
        provider_run_id="xrun-test",
        credential=credential,
        payload=payload,
        body=ExternalAgentRunRequest(provider=ExternalAgentProvider.CODEX, mode="patch", prompt="fix"),
        model="gpt-5.4",
    )

    assert result.status == "unavailable"
    assert "not installed" in result.summary
    assert result.files_changed == []
    assert (workspace / "app.py").read_text(encoding="utf-8") == original


def test_external_cli_patch_adapter_redacts_secret_like_output(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)

    def failing_runner(args, *, cwd, capture_output, text, timeout, shell, env):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="using sk-test-secret-value-123456",
            stderr="remote https://ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/voiceops/demo.git",
        )

    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-redaction-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    credential, payload = _credential(ExternalAgentProvider.CLAUDE, command="claude")
    payload["command_template"] = "python agent.py --workspace {workspace} --api-key sk-test-secret-value-123456"

    result = ExternalCodingAgentAdapter(settings, runner=failing_runner).propose_patch(
        provider_run_id="xrun-redact",
        credential=credential,
        payload=payload,
        body=ExternalAgentRunRequest(provider=ExternalAgentProvider.CLAUDE, mode="patch", prompt="fix"),
        model="claude-sonnet-4-6",
    )
    audit = str(result.audit)

    assert result.status == "failed"
    assert "sk-test-secret" not in audit
    assert "ghp_" not in audit
    assert "[redacted]" in audit


def test_external_cli_patch_adapter_supports_command_templates(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    calls = []

    def fake_runner(args, *, cwd, capture_output, text, timeout, shell, env):
        calls.append(args)
        assert args[0:2] == ["python", "agent.py"]
        assert args[args.index("--workspace") + 1] == str(cwd)
        assert args[args.index("--prompt") + 1] == "add a ready endpoint"
        assert args[args.index("--model") + 1] == "claude-sonnet-4-6"
        target = cwd / "app.py"
        target.write_text(
            target.read_text(encoding="utf-8")
            + '\n\n@app.get("/ready")\ndef ready():\n    return {"status": "ready"}\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="templated", stderr="")

    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-template-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    credential, payload = _credential(
        ExternalAgentProvider.CLAUDE,
        command="claude",
    )
    payload["command_template"] = "python agent.py --workspace {workspace} --prompt {prompt} --model {model}"

    result = ExternalCodingAgentAdapter(settings, runner=fake_runner).propose_patch(
        provider_run_id="xrun-template",
        credential=credential,
        payload=payload,
        body=ExternalAgentRunRequest(
            provider=ExternalAgentProvider.CLAUDE,
            mode="patch",
            model="claude-sonnet-4-6",
            prompt="add a ready endpoint",
        ),
        model="claude-sonnet-4-6",
    )

    assert result.status == "completed"
    assert result.files_changed == ["app.py"]
    assert calls
    assert result.audit["command"][0:2] == ["python", "agent.py"]
    assert "--prompt" in result.audit["command"]


def test_external_cli_patch_adapter_fails_legacy_template_without_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-template-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    credential, payload = _credential(ExternalAgentProvider.CLAUDE, command="claude")
    payload["command_template"] = "python agent.py --prompt {prompt}"

    result = ExternalCodingAgentAdapter(settings).propose_patch(
        provider_run_id="xrun-template",
        credential=credential,
        payload=payload,
        body=ExternalAgentRunRequest(
            provider=ExternalAgentProvider.CLAUDE,
            mode="patch",
            model="claude-sonnet-4-6",
            prompt="add a ready endpoint",
        ),
        model="claude-sonnet-4-6",
    )

    assert result.status == "failed"
    assert result.error == "External agent command_template must include {workspace}"


def test_external_agent_api_key_is_redacted_and_encrypted(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-test-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    events = RoomEventHub()

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: events
    try:
        with TestClient(app) as client:
            token = _token(client)
            response = client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=_auth(token),
                json={"api_key": "sk-test-secret-value-123456", "account_label": "Claude Team"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["provider"] == "claude"
            assert body["auth_method"] == "api_key"
            assert body["token_preview"] == "sk-t...3456"
            assert "sk-test-secret-value" not in response.text
            assert "sk-test-secret-value" not in settings.external_agent_store_path.read_text(encoding="utf-8")
            assert settings.external_agent_store_path.stat().st_mode & 0o777 == 0o600

            providers = client.get("/external-agents/providers", headers=_auth(token)).json()
            claude = next(item for item in providers if item["provider"] == "claude")
            assert claude["connected"] is True
            assert claude["token_preview"] == "sk-t...3456"
            assert claude["default_model"] == "claude-sonnet-4-6"
            assert {
                "claude-fable-5",
                "claude-mythos-5",
                "claude-sonnet-4-6",
                "claude-sonnet-4-5",
                "claude-opus-4-8",
                "claude-haiku-4-5",
                "claude-haiku-4-5-20251001",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-opus-4-5",
                "claude-sonnet",
                "claude-opus",
                "claude-haiku",
            }.issubset(set(claude["supported_models"]))
            codex = next(item for item in providers if item["provider"] == "codex")
            assert codex["default_model"] == "gpt-5.4"
            assert {
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-5",
                "gpt-5-mini",
                "gpt-5-nano",
            }.issubset(set(codex["supported_models"]))
            assert claude["mode_readiness"]["patch"]["ready"] is False
            assert claude["mode_readiness"]["patch"]["reason"] == "local_cli_required"
            assert claude["mode_readiness"]["patch"]["auth_method"] == "api_key"
            assert claude["mode_readiness"]["patch"]["preview_first"] is True
            assert claude["mode_readiness"]["review"]["ready"] is True
            assert claude["mode_readiness"]["review"]["reason"] == "mock_adapter"
            assert claude["mode_readiness"]["review"]["severity"] == "warning"
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_provider_catalogue_exposes_all_coding_agent_models(tmp_path):
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-provider-catalogue-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=None,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            response = client.get("/external-agents/providers", headers=_auth(token))
            assert response.status_code == 200
            providers = {item["provider"]: item for item in response.json()}

            assert set(providers) == {"claude", "codex", "cursor", "local"}
            assert providers["claude"]["default_model"] == "claude-sonnet-4-6"
            assert {
                "claude-fable-5",
                "claude-mythos-5",
                "claude-sonnet-4-6",
                "claude-sonnet-4-5",
                "claude-opus-4-8",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-opus-4-5",
                "claude-haiku-4-5",
                "claude-haiku-4-5-20251001",
                "claude-sonnet",
                "claude-opus",
                "claude-haiku",
                "provider-default",
            }.issubset(set(providers["claude"]["supported_models"]))

            assert providers["codex"]["default_model"] == "gpt-5.4"
            assert {
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-5",
                "gpt-5-mini",
                "gpt-5-nano",
                "gpt-default",
                "provider-default",
            }.issubset(set(providers["codex"]["supported_models"]))

            assert providers["cursor"]["default_model"] == "cursor-default"
            assert {"cursor-default", "provider-default"}.issubset(set(providers["cursor"]["supported_models"]))
            assert providers["cursor"]["auth_methods"] == ["local_cli"]
            assert providers["local"]["default_model"] == "local-default"
            assert {
                "local-default",
                "glm-5.2-local",
                "qwen-coder-local",
                "deepseek-coder-local",
                "provider-default",
            }.issubset(set(providers["local"]["supported_models"]))
            assert providers["local"]["auth_methods"] == ["local_cli"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_auto_external_assignment_dispatch_uses_provider_recommendation(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-auto-assignment-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/local/credentials/local-cli",
                headers=headers,
                json={"account_label": "Local Open Agent"},
            ).status_code == 200

            created = client.post(
                "/agents/rooms/main/assignments",
                headers=headers,
                json={
                    "agent_id": "auto",
                    "agent_label": "Auto external agent",
                    "agent_kind": "external",
                    "task": "fix the health endpoint",
                    "mode": "patch",
                },
            )
            assert created.status_code == 200
            created_data = created.json()
            assert created_data["metadata"]["routing_source"] == "external_agent_recommendation"
            assert created_data["metadata"]["recommended_provider"] == "local"
            assert created_data["metadata"]["recommended_mode"] == "patch"
            assert created_data["metadata"]["recommended_task_kind"] == "write_patch"
            assert created_data["metadata"]["recommendation_ready"] is True

            dispatched = client.post(
                f"/agents/rooms/main/assignments/{created_data['id']}/dispatch",
                headers=headers,
            )

            assert dispatched.status_code == 200
            data = dispatched.json()
            assert data["status"] == "completed"
            assert data["action_id"]
            assert data["metadata"]["routing_source"] == "external_agent_recommendation"
            assert data["metadata"]["recommended_provider"] == "local"
            assert data["metadata"]["recommended_mode"] == "patch"
            assert data["metadata"]["recommended_task_kind"] == "write_patch"
            assert data["metadata"]["provider"] == "local"
            assert data["metadata"]["model"] == "local-default"
            assert data["metadata"]["provider_status"] == "pending_approval"
            assert (workspace / "app.py").read_text(encoding="utf-8") == original

            room = client.get("/collab/rooms/main", headers=headers).json()
            action = next(item for item in room["actions"] if item["id"] == data["action_id"])
            assert action["status"] == "pending_approval"
            assert action["approval"]["external_agent"]["provider"] == "local"
            assignment_messages = [
                message for message in room["messages"]
                if message["metadata"].get("source") == "agent_assignment"
            ]
            assert assignment_messages[-1]["metadata"]["recommended_provider"] == "local"
            assert assignment_messages[-1]["metadata"]["recommendation_ready"] is True
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_local_cli_rejects_unsafe_command_template(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-template-validation-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            headers = _auth(_token(client))
            missing_workspace = client.post(
                "/external-agents/providers/claude/credentials/local-cli",
                headers=headers,
                json={"command": "python agent.py", "command_template": "python agent.py --prompt {prompt}"},
            )
            bad_quote = client.post(
                "/external-agents/providers/claude/credentials/local-cli",
                headers=headers,
                json={"command": "python agent.py", "command_template": "python agent.py --workspace {workspace} --prompt '"},
            )
            token_template = client.post(
                "/external-agents/providers/claude/credentials/local-cli",
                headers=headers,
                json={
                    "command": "python agent.py",
                    "command_template": "python agent.py --workspace {workspace} --api-key sk-test-secret-value-123456",
                },
            )
            cookie_command = client.post(
                "/external-agents/providers/claude/credentials/local-cli",
                headers=headers,
                json={"command": "claude --cookie browser-session-token", "command_template": "claude --workspace {workspace}"},
            )

            assert missing_workspace.status_code == 400
            assert "{workspace}" in missing_workspace.json()["detail"]
            assert bad_quote.status_code == 400
            assert "Invalid local CLI command_template" in bad_quote.json()["detail"]
            assert token_template.status_code == 400
            assert "must not include tokens" in token_template.json()["detail"]
            assert cookie_command.status_code == 400
            assert "browser cookies" in cookie_command.json()["detail"]
            assert not settings.external_agent_store_path.exists()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_local_cli_defaults_provider_command_template(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-default-template-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            headers = _auth(_token(client))
            connected = client.post(
                "/external-agents/providers/claude/credentials/local-cli",
                headers=headers,
                json={"command": "claude"},
            )
            assert connected.status_code == 200
            assert connected.json()["metadata"]["command_template"] == (
                "claude --workspace {workspace} --prompt {prompt} --model {model}"
            )

            providers = client.get("/external-agents/providers", headers=headers)
            assert providers.status_code == 200
            claude = next(item for item in providers.json() if item["provider"] == "claude")
            assert claude["local_cli_command"] == "claude"
            assert claude["local_cli_command_template"] == (
                "claude --workspace {workspace} --prompt {prompt} --model {model}"
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_codex_local_cli_keeps_special_exec_default(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-codex-default-template-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            headers = _auth(_token(client))
            connected = client.post(
                "/external-agents/providers/codex/credentials/local-cli",
                headers=headers,
                json={"command": "codex"},
            )
            assert connected.status_code == 200
            assert connected.json()["metadata"]["command_template"] is None

            providers = client.get("/external-agents/providers", headers=headers)
            assert providers.status_code == 200
            codex = next(item for item in providers.json() if item["provider"] == "codex")
            assert codex["local_cli_command"] == "codex"
            assert codex["local_cli_command_template"] is None
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_local_cli_defaults_template_from_custom_command(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-custom-template-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            headers = _auth(_token(client))
            connected = client.post(
                "/external-agents/providers/local/credentials/local-cli",
                headers=headers,
                json={"command": "python local_agent.py"},
            )
            assert connected.status_code == 200
            assert connected.json()["metadata"]["command"] == "python local_agent.py"
            assert connected.json()["metadata"]["command_template"] == (
                "python local_agent.py --workspace {workspace} --prompt {prompt} --model {model}"
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_oauth_state_and_callback_connects(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-oauth-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            start = client.post(
                "/external-agents/providers/codex/oauth/start",
                headers=_auth(token),
                json={"scopes": ["codex:run"]},
            )
            assert start.status_code == 200
            started = start.json()
            assert started["mode"] == "mock_exchange"
            assert "state=" in started["authorization_url"]

            bad = client.post(
                "/external-agents/oauth/callback",
                headers=_auth(token),
                json={"provider": "codex", "code": "abc", "state": "wrong-state-value"},
            )
            assert bad.status_code == 400

            done = client.post(
                "/external-agents/oauth/callback",
                headers=_auth(token),
                json={"provider": "codex", "code": "abc", "state": started["state"]},
            )
            assert done.status_code == 200
            assert done.json()["auth_method"] == "oauth"
            assert done.json()["metadata"]["exchange_mode"] == "mock_exchange"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_oauth_mock_exchange_can_be_disabled(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-oauth-no-mock-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        external_agent_oauth_mock_enabled=False,
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    external_agent_service._pending_oauth_states.clear()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            start = client.post(
                "/external-agents/providers/codex/oauth/start",
                headers=_auth(token),
                json={"scopes": ["codex:run"]},
            )

            assert start.status_code == 409
            assert "OAuth is not configured" in start.json()["detail"]
            assert external_agent_service._pending_oauth_states == {}
            assert not settings.external_agent_store_path.exists()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_oauth_state_expires(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-oauth-expiry-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            started = client.post(
                "/external-agents/providers/codex/oauth/start",
                headers=_auth(token),
                json={"scopes": ["codex:run"]},
            ).json()
            state = started["state"]
            external_agent_service._pending_oauth_states[state]["created_at"] = (
                datetime.now(UTC) - timedelta(minutes=11)
            ).isoformat()

            done = client.post(
                "/external-agents/oauth/callback",
                headers=_auth(token),
                json={"provider": "codex", "code": "abc", "state": state},
            )

            assert done.status_code == 400
            assert state not in external_agent_service._pending_oauth_states
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_oauth_callback_respects_current_provider_allowlist(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-oauth-allowlist-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_allowed_providers=["codex"],
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            started = client.post(
                "/external-agents/providers/codex/oauth/start",
                headers=_auth(token),
                json={"scopes": ["codex:run"]},
            ).json()
            settings.external_agent_allowed_providers = ["local"]

            done = client.post(
                "/external-agents/oauth/callback",
                headers=_auth(token),
                json={"provider": "codex", "code": "abc", "state": started["state"]},
            )

            assert done.status_code == 404
            assert "not enabled" in done.json()["detail"]
            assert not settings.external_agent_store_path.exists()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_oauth_configured_mode_exchanges_code(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-oauth-configured-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        codex_oauth_client_id="client-id",
        codex_oauth_client_secret="client-secret",
        codex_oauth_token_url="https://example.test/token",
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"access_token": "real-access-token-123456", "refresh_token": "refresh-token", "expires_in": 3600}

    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, data):
            calls.append({"url": url, "data": data, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr("app.external_agents.service.httpx.AsyncClient", FakeAsyncClient)

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            start = client.post(
                "/external-agents/providers/codex/oauth/start",
                headers=_auth(token),
                json={"scopes": ["codex:run"]},
            ).json()
            assert start["mode"] == "configured"

            done = client.post(
                "/external-agents/oauth/callback",
                headers=_auth(token),
                json={"provider": "codex", "code": "auth-code", "state": start["state"]},
            )
            assert done.status_code == 200
            assert calls[0]["url"] == "https://example.test/token"
            assert calls[0]["data"]["code"] == "auth-code"
            assert "real-access-token" not in done.text
            assert "real-access-token" not in settings.external_agent_store_path.read_text(encoding="utf-8")
            assert done.json()["metadata"]["exchange_mode"] == "configured"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_patch_run_creates_pending_approval_without_writing(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-run-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            connected = client.post(
                "/external-agents/providers/cursor/credentials/local-cli",
                headers=headers,
                json={"account_label": "Cursor local"},
            )
            assert connected.status_code == 200

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "cursor",
                    "mode": "patch",
                    "model": "cursor-default",
                    "prompt": "fix the health endpoint",
                },
            )
            assert run.status_code == 200
            result = run.json()
            assert result["status"] == "pending_approval"
            assert result["model"] == "cursor-default"
            assert result["action_id"]
            assert result["files_changed"] == ["app.py"]
            assert "--- a/app.py" in result["diff"]
            assert result["audit"]["runtime"]["execution_mode"] == "mock_patch_adapter"
            assert (workspace / "app.py").read_text(encoding="utf-8") == original

            room = client.get("/collab/rooms/main", headers=headers).json()
            action = next(item for item in room["actions"] if item["id"] == result["action_id"])
            assert action["status"] == "pending_approval"
            assert action["approval"]["external_agent"]["provider"] == "cursor"
            assert action["approval"]["external_agent"]["model"] == "cursor-default"
            assert action["approval"]["model"] == "cursor-default"
            assert action["approval"]["policy"] == "approval_first"
            assert any(
                message["metadata"].get("source") == "external_agent_run"
                and message["metadata"].get("model") == "cursor-default"
                for message in room["messages"]
            )
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_patch_run_uses_local_cli_preview_when_enabled(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    (workspace / "patch_cli.py").write_text(
        textwrap.dedent(
            """
            from pathlib import Path

            path = Path("app.py")
            source = path.read_text(encoding="utf-8")
            path.write_text(source + '\\n\\n@app.get("/ready")\\ndef ready():\\n    return {"status": "ready"}\\n', encoding="utf-8")
            print("prepared ready endpoint")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    original = (workspace / "app.py").read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-cli-preview-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/codex/credentials/local-cli",
                headers=headers,
                json={"command": "python patch_cli.py", "account_label": "Codex local"},
            ).status_code == 200

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "codex",
                    "mode": "patch",
                    "model": "gpt-5.4",
                    "prompt": "add readiness endpoint",
                },
            )

            assert run.status_code == 200
            result = run.json()
            assert result["status"] == "pending_approval"
            assert result["files_changed"] == ["app.py"]
            assert "ready" in result["diff"]
            assert result["audit"]["runtime"]["execution_mode"] == "local_cli_patch"
            assert result["audit"]["runtime"]["workspace"] == "temporary"
            assert (workspace / "app.py").read_text(encoding="utf-8") == original

            action = next(
                item for item in client.get("/collab/rooms/main", headers=headers).json()["actions"]
                if item["id"] == result["action_id"]
            )
            assert action["approval"]["runtime"]["execution_mode"] == "local_cli_patch"
            assert action["approval"]["runtime"]["workspace"] == "temporary"
            assert action["approval"]["external_agent"]["provider"] == "codex"
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_read_only_run_preserves_selected_model(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-model-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-test-secret-value-123456", "account_label": "Claude Team"},
            ).status_code == 200

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "claude",
                    "mode": "review",
                    "model": "claude-opus",
                    "prompt": "review the latest patch",
                },
            )
            result = run.json()
            room = client.get("/collab/rooms/main", headers=headers).json()

            assert run.status_code == 200
            assert result["status"] == "completed"
            assert result["model"] == "claude-opus"
            assert result["audit"]["model"] == "claude-opus"
            assert room["messages"][-1]["metadata"]["provider"] == "claude"
            assert room["messages"][-1]["metadata"]["model"] == "claude-opus"
            assert "claude-opus" in room["messages"][-1]["text"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_api_key_patch_requires_local_cli_even_with_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-api-key-patch-blocked-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-test-secret-value-123456", "account_label": "Claude Team"},
            ).status_code == 200

            providers = client.get("/external-agents/providers", headers=headers).json()
            claude = next(item for item in providers if item["provider"] == "claude")
            assert claude["mode_readiness"]["patch"]["ready"] is False
            assert claude["mode_readiness"]["patch"]["reason"] == "local_cli_required"

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "claude",
                    "mode": "patch",
                    "model": "claude-sonnet-4-6",
                    "prompt": "fix app.py",
                },
            )
            assert run.status_code == 409
            assert "local CLI credential" in run.json()["detail"]
            assert (workspace / "app.py").read_text(encoding="utf-8") == original
            assert client.get("/collab/rooms/main", headers=headers).json()["actions"] == []
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_preflight_reports_blockers_and_safe_runtime_evidence(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    secret = "sk-test-secret-value-123456"
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-preflight-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": secret, "account_label": "Claude Team"},
            ).status_code == 200
            assert client.post(
                "/external-agents/providers/codex/credentials/local-cli",
                headers=headers,
                json={"command": "definitely-missing-codex-cli", "account_label": "Codex CLI"},
            ).status_code == 200

            response = client.get("/external-agents/preflight", headers=headers)

            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "needs_attention"
            assert body["ready"] is False
            assert secret not in response.text
            claude = next(item for item in body["providers"] if item["provider"] == "claude")
            assert claude["state"] == "degraded"
            assert claude["evidence"]["tokens_returned_to_browser"] is False
            assert claude["evidence"]["credential_storage"] == "encrypted_server_side"
            assert "local CLI credential" in " ".join(claude["blockers"])
            codex = next(item for item in body["providers"] if item["provider"] == "codex")
            assert codex["state"] == "blocked"
            assert codex["evidence"]["local_cli_runtime"] == "isolated_temp_workspace"
            assert codex["evidence"]["env_policy"] == "minimal_external_agent_env"
            assert "VOICEOPS_AGENT_PROMPT" in codex["evidence"]["safe_env_keys"]
            assert codex["evidence"]["cli_available"] is False
            assert any(mode["reason"] == "cli_missing" for mode in codex["modes"])
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_setup_guide_explains_auth_paths_and_patch_constraints(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-setup-guide-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            first = client.get("/external-agents/setup-guide", headers=headers)
            assert first.status_code == 200
            codex = next(item for item in first.json()["providers"] if item["provider"] == "codex")
            assert codex["state"] == "not_connected"
            assert codex["recommended_path"] == "local_cli_for_code_changes"
            assert codex["next_step"].startswith("Connect local CLI")
            assert any(step["id"] == "connect_local_cli" and step["recommended"] for step in codex["steps"])
            assert any(step["id"] == "connect_oauth" for step in codex["steps"])
            assert "Patch mode always stays preview-first" in codex["constraints"][0]

            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-test-secret-value-123456", "account_label": "Claude Team"},
            ).status_code == 200
            providers = client.get("/external-agents/providers", headers=headers).json()
            claude = next(item for item in providers if item["provider"] == "claude")
            guide = claude["setup_guide"]
            assert guide["recommended_path"] == "api_or_oauth_read_only"
            assert guide["next_step"] == "Add a local CLI credential before assigning patch work."
            local_cli = next(step for step in guide["steps"] if step["id"] == "connect_local_cli")
            assert local_cli["auth_method"] == "local_cli"
            assert "patch" in local_cli["modes"]
            assert claude["token_preview"] == "sk-t...3456"
            assert "sk-test-secret-value" not in client.get("/external-agents/setup-guide", headers=headers).text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_cursor_and_local_open_agent_providers_use_local_cli_only(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-local-provider-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            providers = client.get("/external-agents/providers", headers=headers)
            assert providers.status_code == 200
            local = next(item for item in providers.json() if item["provider"] == "local")
            cursor = next(item for item in providers.json() if item["provider"] == "cursor")
            assert cursor["label"] == "Cursor Agent"
            assert cursor["auth_methods"] == ["local_cli"]
            assert cursor["default_model"] == "cursor-default"
            assert [step["id"] for step in cursor["setup_guide"]["steps"]] == ["connect_local_cli"]
            assert cursor["setup_guide"]["steps"][0]["command"] == "cursor-agent"
            assert local["label"] == "Local Open Agent"
            assert local["auth_methods"] == ["local_cli"]
            assert local["default_model"] == "local-default"
            assert {"local-default", "glm-5.2-local", "qwen-coder-local", "deepseek-coder-local"}.issubset(set(local["supported_models"]))
            assert local["setup_guide"]["next_step"] == "Connect a local CLI command for this open-source or self-hosted coding agent."
            assert [step["id"] for step in local["setup_guide"]["steps"]] == ["connect_local_cli"]
            assert local["setup_guide"]["steps"][0]["command"] == "local-agent"

            api_key = client.post(
                "/external-agents/providers/local/credentials/api-key",
                headers=headers,
                json={"api_key": "local-secret-value"},
            )
            assert api_key.status_code == 400
            assert "local_cli" in api_key.json()["detail"]

            oauth = client.post(
                "/external-agents/providers/local/oauth/start",
                headers=headers,
                json={},
            )
            assert oauth.status_code == 400
            assert "local_cli" in oauth.json()["detail"]

            cursor_api_key = client.post(
                "/external-agents/providers/cursor/credentials/api-key",
                headers=headers,
                json={"api_key": "cursor-secret-value"},
            )
            assert cursor_api_key.status_code == 400
            assert "local_cli" in cursor_api_key.json()["detail"]

            cursor_oauth = client.post(
                "/external-agents/providers/cursor/oauth/start",
                headers=headers,
                json={},
            )
            assert cursor_oauth.status_code == 400
            assert "local_cli" in cursor_oauth.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_capabilities_and_recommendations_route_by_task_kind(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-recommendation-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            viewer_headers = _auth(_token_for(client, "viewer@voiceops.dev", "view123"))
            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-test-secret-value-123456", "account_label": "Claude Team"},
            ).status_code == 200
            assert client.post(
                "/external-agents/providers/local/credentials/local-cli",
                headers=headers,
                json={"account_label": "Local Open Agent"},
            ).status_code == 200

            capabilities = client.get("/external-agents/capabilities", headers=headers)
            assert capabilities.status_code == 200
            body = capabilities.json()
            assert "write_patch" in body["task_kinds"]
            local = next(item for item in body["providers"] if item["provider"] == "local")
            assert local["auth_methods"] == ["local_cli"]
            assert local["patch_requires_approval"] is True
            assert local["patch_requires_local_cli"] is True
            assert "open-source/self-hosted coding agent" in local["best_for"]
            assert "write_patch" in local["task_kinds"]

            viewer_recommendation = client.post(
                "/external-agents/recommendations",
                headers=viewer_headers,
                json={"task": "recommend an agent for this patch"},
            )
            assert viewer_recommendation.status_code == 403

            explain = client.post(
                "/external-agents/recommendations",
                headers=headers,
                json={"task": "explain how app.py works"},
            )
            assert explain.status_code == 200
            explain_body = explain.json()
            assert explain_body["task_kind"] == "explain_code"
            assert explain_body["provider"] == "claude"
            assert explain_body["mode"] == "explain"
            assert explain_body["approval_required"] is False
            assert explain_body["ready"] is True

            patch = client.post(
                "/external-agents/recommendations",
                headers=headers,
                json={"task": "AI fix that readiness endpoint", "preferred_provider": "local"},
            )
            assert patch.status_code == 200
            patch_body = patch.json()
            assert patch_body["task_kind"] == "write_patch"
            assert patch_body["provider"] == "local"
            assert patch_body["mode"] == "patch"
            assert patch_body["approval_required"] is True
            assert patch_body["ready"] is True
            assert any(item["provider"] == "claude" for item in patch_body["alternatives"])
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_read_only_local_cli_runs_in_isolated_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    (workspace / "agent_cli.py").write_text(
        "import os\nimport sys\n"
        "print('model=' + os.environ['VOICEOPS_AGENT_MODEL'])\n"
        "print('prompt=' + os.environ['VOICEOPS_AGENT_PROMPT'])\n"
        "print('argv=' + ' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-local-cli-runtime-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            connected = client.post(
                "/external-agents/providers/claude/credentials/local-cli",
                headers=headers,
                json={
                    "command": "python agent_cli.py",
                    "command_template": "python agent_cli.py --workspace {workspace} --prompt {prompt} --model {model}",
                    "account_label": "Claude CLI",
                },
            )
            assert connected.status_code == 200
            providers = client.get("/external-agents/providers", headers=headers).json()
            claude = next(item for item in providers if item["provider"] == "claude")
            assert claude["local_cli_command"] == "python agent_cli.py"
            assert "{workspace}" in claude["local_cli_command_template"]
            assert claude["mode_readiness"]["review"]["ready"] is True
            assert claude["mode_readiness"]["review"]["reason"] == "cli_ready"
            assert claude["mode_readiness"]["review"]["execution_mode"] == "local_cli"
            assert claude["mode_readiness"]["review"]["cli_available"] is True
            assert claude["mode_readiness"]["patch"]["ready"] is True
            assert claude["mode_readiness"]["patch"]["reason"] == "preview_first_cli"
            assert claude["mode_readiness"]["patch"]["preview_first"] is True

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "claude",
                    "mode": "review",
                    "model": "claude-opus-4-8",
                    "prompt": "review app.py",
                },
            )

            assert run.status_code == 200
            result = run.json()
            assert result["status"] == "completed"
            assert result["audit"]["execution_mode"] == "local_cli"
            assert result["audit"]["runtime"]["runtime"] == "isolated_temp_workspace"
            assert result["audit"]["runtime"]["workspace"] == "temporary"
            assert "--workspace" in result["audit"]["runtime"]["command"]
            assert "--prompt 'review app.py'" in result["audit"]["runtime"]["command"]
            assert "--model claude-opus-4-8" in result["audit"]["runtime"]["command"]
            assert "model=claude-opus-4-8" in result["summary"]
            assert "prompt=review app.py" in result["summary"]
            assert "argv=--workspace" in result["summary"]
            room = client.get("/collab/rooms/main", headers=headers).json()
            assert room["messages"][-1]["metadata"]["execution_mode"] == "local_cli"
            assert room["messages"][-1]["metadata"]["model"] == "claude-opus-4-8"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_local_cli_readiness_blocks_missing_binary(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-missing-cli-readiness-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/codex/credentials/local-cli",
                headers=headers,
                json={"command": "voiceops-missing-cli-binary-xyz", "account_label": "Missing Codex CLI"},
            ).status_code == 200

            providers = client.get("/external-agents/providers", headers=headers).json()
            codex = next(item for item in providers if item["provider"] == "codex")
            assert codex["mode_readiness"]["review"]["ready"] is False
            assert codex["mode_readiness"]["review"]["reason"] == "cli_missing"
            assert codex["mode_readiness"]["review"]["cli_available"] is False
            assert codex["mode_readiness"]["patch"]["ready"] is False
            assert codex["mode_readiness"]["patch"]["reason"] == "cli_missing"
            assert codex["mode_readiness"]["patch"]["preview_first"] is True

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "codex",
                    "mode": "review",
                    "model": "gpt-5.4",
                    "prompt": "review app.py",
                },
            )
            assert run.status_code == 409
            assert "not installed" in run.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_claude_api_adapter_runs_read_only_when_enabled(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeProviderResponse(
            {
                "id": "msg-test",
                "model": json["model"],
                "content": [{"type": "text", "text": "Review looks good."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 4},
            }
        )

    monkeypatch.setattr("app.external_agents.adapters.httpx.post", fake_post)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-claude-api-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_api_execution_enabled=True,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-ant-test-secret-123456"},
            ).status_code == 200

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "claude",
                    "mode": "review",
                    "model": "claude-opus-4-8",
                    "prompt": "review app.py",
                },
            )

            assert run.status_code == 200
            data = run.json()
            assert data["status"] == "completed"
            assert data["audit"]["execution_mode"] == "anthropic_messages_api"
            assert data["audit"]["response_id"] == "msg-test"
            assert "Review looks good." in data["summary"]
            assert "sk-ant-test-secret" not in run.text
            assert calls[0]["url"].endswith("/v1/messages")
            assert calls[0]["headers"]["x-api-key"] == "sk-ant-test-secret-123456"
            assert calls[0]["headers"]["anthropic-version"] == settings.anthropic_api_version
            assert calls[0]["json"]["model"] == "claude-opus-4-8"
            assert calls[0]["json"]["messages"][0]["content"] == "review app.py"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_codex_api_adapter_runs_openai_responses_when_enabled(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeProviderResponse(
            {
                "id": "resp-test",
                "model": json["model"],
                "status": "completed",
                "output_text": "Use a smaller patch.",
                "usage": {"input_tokens": 8, "output_tokens": 5},
            }
        )

    monkeypatch.setattr("app.external_agents.adapters.httpx.post", fake_post)
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-openai-api-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=str(workspace),
        external_agent_api_execution_enabled=True,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/codex/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-openai-test-secret-123456"},
            ).status_code == 200

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={
                    "provider": "codex",
                    "mode": "explain",
                    "model": "gpt-5.4",
                    "prompt": "explain app.py",
                },
            )

            assert run.status_code == 200
            data = run.json()
            assert data["status"] == "completed"
            assert data["audit"]["execution_mode"] == "openai_responses_api"
            assert data["audit"]["response_id"] == "resp-test"
            assert "Use a smaller patch." in data["summary"]
            assert "sk-openai-test-secret" not in run.text
            assert calls[0]["url"].endswith("/responses")
            assert calls[0]["headers"]["authorization"] == "Bearer sk-openai-test-secret-123456"
            assert calls[0]["json"]["model"] == "gpt-5.4"
            assert calls[0]["json"]["input"] == "explain app.py"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_patch_requires_workspace(tmp_path):
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-no-workspace-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=None,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post(
                "/external-agents/providers/claude/credentials/api-key",
                headers=headers,
                json={"api_key": "sk-test-secret-value-123456"},
            ).status_code == 200

            run = client.post(
                "/external-agents/rooms/main/runs",
                headers=headers,
                json={"provider": "claude", "mode": "patch", "prompt": "fix app.py"},
            )
            assert run.status_code == 409
            assert "VOICEOPS_WORKSPACE" in run.json()["detail"]

            providers = client.get("/external-agents/providers", headers=headers).json()
            claude = next(item for item in providers if item["provider"] == "claude")
            assert claude["mode_readiness"]["patch"]["ready"] is False
            assert claude["mode_readiness"]["patch"]["reason"] == "workspace_missing"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_provider_readiness_contract_for_disconnected_provider(tmp_path):
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-readiness-contract-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        voiceops_workspace=None,
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            providers = client.get("/external-agents/providers", headers=_auth(token))
            assert providers.status_code == 200
            cursor = next(item for item in providers.json() if item["provider"] == "cursor")
            assert cursor["connected"] is False
            assert set(cursor["mode_readiness"]) == {"explain", "patch", "review", "test"}
            for readiness in cursor["mode_readiness"].values():
                assert readiness["ready"] is False
                assert readiness["reason"] == "not_connected"
                assert readiness["severity"] == "warning"
                assert readiness["credential_status"] == "disconnected"
                assert readiness["auth_method"] is None
                assert readiness["model_required"] is True
                assert "Connect this provider" in readiness["detail"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_assignment_dispatch_preflights_patch_target(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# no app target\n", encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-assignment-preflight-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/cursor/credentials/local-cli",
                headers=headers,
                json={"account_label": "Cursor local"},
            ).status_code == 200
            created = client.post(
                "/agents/rooms/main/assignments",
                headers=headers,
                json={
                    "agent_id": "cursor",
                    "agent_label": "Cursor Agent",
                    "agent_kind": "external",
                    "task": "fix the health endpoint",
                    "mode": "patch",
                },
            )
            assert created.status_code == 200
            dispatched = client.post(
                f"/agents/rooms/main/assignments/{created.json()['id']}/dispatch",
                headers=headers,
            )
            assert dispatched.status_code == 200
            data = dispatched.json()
            assert data["status"] == "failed"
            assert data["action_id"] is None
            assert data["metadata"]["readiness"]["reason"] == "patch_target_missing"
            assert client.get("/collab/rooms/main", headers=headers).json()["actions"] == []
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_assignment_dispatch_creates_pending_approval_without_writing(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-agent-assignment-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/cursor/credentials/local-cli",
                headers=headers,
                json={"account_label": "Cursor local"},
            ).status_code == 200

            created = client.post(
                "/agents/rooms/main/assignments",
                headers=headers,
                json={
                    "agent_id": "cursor",
                    "agent_label": "Cursor Agent",
                    "agent_kind": "external",
                    "task": "fix the health endpoint",
                    "mode": "patch",
                },
            )
            assert created.status_code == 200
            assignment_id = created.json()["id"]

            dispatched = client.post(
                f"/agents/rooms/main/assignments/{assignment_id}/dispatch",
                headers=headers,
            )
            assert dispatched.status_code == 200
            data = dispatched.json()
            assert data["status"] == "completed"
            assert data["run_id"].startswith("xrun-")
            assert data["action_id"]
            assert data["metadata"]["provider_status"] == "pending_approval"
            assert data["metadata"]["files_changed"] == ["app.py"]
            assert (workspace / "app.py").read_text(encoding="utf-8") == original

            room = client.get("/collab/rooms/main", headers=headers).json()
            action = next(item for item in room["actions"] if item["id"] == data["action_id"])
            assert action["status"] == "pending_approval"
            assert action["approval"]["external_agent"]["provider"] == "cursor"
            assignment_messages = [
                message for message in room["messages"]
                if message["metadata"].get("source") == "agent_assignment"
            ]
            assert assignment_messages[-1]["metadata"]["provider_status"] == "pending_approval"
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_external_agent_assignment_uses_room_workspace_for_patch_and_approval(tmp_path):
    workspace = tmp_path / "workspace"
    room_workspace = tmp_path / "room-workspace"
    _write_workspace(workspace)
    _write_workspace(room_workspace)
    _init_git(workspace)
    _init_git(room_workspace)
    global_app = workspace / "app.py"
    room_app = room_workspace / "app.py"
    global_original = global_app.read_text(encoding="utf-8")
    room_original = room_app.read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="external-room-workspace-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            admin = _token_for(client, "admin@voiceops.dev", "admin123")
            headers = _auth(token)
            admin_headers = _auth(admin)
            assert client.post("/collab/rooms/room-b/join", headers=headers, json={}).status_code == 200
            assert client.put(
                "/collab/rooms/room-b/workspace",
                headers=admin_headers,
                json={"path": str(room_workspace)},
            ).status_code == 200
            assert client.post(
                "/external-agents/providers/cursor/credentials/local-cli",
                headers=headers,
                json={"account_label": "Cursor local"},
            ).status_code == 200
            created = client.post(
                "/agents/rooms/room-b/assignments",
                headers=headers,
                json={
                    "agent_id": "cursor",
                    "agent_label": "Cursor Agent",
                    "agent_kind": "external",
                    "task": "fix the health endpoint",
                    "mode": "patch",
                },
            )
            assert created.status_code == 200

            dispatched = client.post(
                f"/agents/rooms/room-b/assignments/{created.json()['id']}/dispatch",
                headers=headers,
            )
            assert dispatched.status_code == 200
            action_id = dispatched.json()["action_id"]
            assert global_app.read_text(encoding="utf-8") == global_original
            assert room_app.read_text(encoding="utf-8") == room_original

            approved = client.post(
                f"/collab/rooms/room-b/actions/{action_id}/approve",
                headers=admin_headers,
                json={},
            )
            assert approved.status_code == 200
            assert global_app.read_text(encoding="utf-8") == global_original
            assert "@app.get(\"/health\")" in room_app.read_text(encoding="utf-8")
            assert approved.json()["approval"]["git"]["branch_name"].startswith(f"voiceops/{action_id}-")
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_voice_best_agent_request_dispatches_auto_external_assignment_to_pending_approval(tmp_path):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    settings = Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="voice-auto-external-assignment-secret",
        external_agent_store_path=tmp_path / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        collab_store_path=tmp_path / "collab-unused.json",
        memory_store_path=str(tmp_path / "memory.json"),
        agent_runs_path=tmp_path / "agent-runs.json",
        voiceops_workspace=str(workspace),
        llm_provider="mock",
        stt_provider="mock",
        tts_provider="mock",
    )
    users = UserStore(settings.users_store_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
    try:
        with TestClient(app) as client:
            token = _token(client)
            headers = _auth(token)
            assert client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200
            assert client.post(
                "/external-agents/providers/local/credentials/local-cli",
                headers=headers,
                json={"account_label": "Local Open Agent"},
            ).status_code == 200

            voice = client.post(
                "/voice/process-text",
                headers=headers,
                json={
                    "text": "AI have the best agent fix that",
                    "session_id": "voice-auto-agent",
                    "room_id": "main",
                    "include_tts": False,
                },
            )

            assert voice.status_code == 200
            data = voice.json()
            assert data["orchestrator_result"] is None
            assert data["response_text"].startswith("Auto external agent proposed a patch change")

            assignments = client.get("/agents/rooms/main/assignments", headers=headers).json()["assignments"]
            assert len(assignments) == 1
            assignment = assignments[0]
            assert assignment["agent_id"] == "auto"
            assert assignment["agent_kind"] == "external"
            assert assignment["agent_label"] == "Auto external agent"
            assert assignment["mode"] == "patch"
            assert assignment["source"] == "voice_auto_external_assignment"
            assert assignment["task"] == "AI have the best agent fix that"
            assert assignment["status"] == "completed"
            assert assignment["action_id"]
            assert assignment["run_id"].startswith("xrun-")
            assert assignment["metadata"]["source"] == "voice_auto_external_assignment"
            assert assignment["metadata"]["voice_transcript"] == "AI have the best agent fix that"
            assert assignment["metadata"]["assignment_origin"] == "voice"
            assert assignment["metadata"]["routing_source"] == "external_agent_recommendation"
            assert assignment["metadata"]["recommended_provider"] == "local"
            assert assignment["metadata"]["recommended_mode"] == "patch"
            assert assignment["metadata"]["recommended_task_kind"] == "write_patch"

            room = client.get("/collab/rooms/main", headers=headers).json()
            assert len(room["actions"]) == 1
            assert room["actions"][0]["id"] == assignment["action_id"]
            assert room["actions"][0]["status"] == "pending_approval"
            assert room["actions"][0]["approval"]["external_agent"]["provider"] == "local"
            assert (workspace / "app.py").read_text(encoding="utf-8") == original
            voice_agent_message = room["messages"][-1]
            assert voice_agent_message["role"] == "agent"
            assert voice_agent_message["metadata"]["source"] == "voice_auto_external_assignment"
            assert voice_agent_message["metadata"]["assignment_id"] == assignment["id"]
            assert voice_agent_message["metadata"]["auto_dispatched"] is True
            assert voice_agent_message["metadata"]["assignment_action_id"] == assignment["action_id"]
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
