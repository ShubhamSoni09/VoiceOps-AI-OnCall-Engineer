from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import textwrap
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

from app.agents.service import MultiAgentService, get_multi_agent_service
from app.agents.store import AgentRunStore
from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.external_agents.service import ExternalAgentService
from app.external_agents.store import ExternalAgentCredentialStore
from app.main import app
import app.voice_agent.router as voice_router


ROOM_ID = "main"
ALICE_EMAIL = "priya@voiceops.dev"
ALICE_PASSWORD = "oncall123"
BOB_EMAIL = "admin@voiceops.dev"
BOB_PASSWORD = "admin123"


class ExternalCodingAgentHarnessFailure(AssertionError):
    """Raised when the external coding agent smoke flow breaks at a named step."""


@dataclass
class ExternalCodingAgentHarnessReport:
    room_id: str
    workspace_path: str
    global_workspace_path: str
    provider: str
    assignment_id: str
    action_id: str
    branch: str
    files_changed: list[str]
    tests_passed: bool
    pending_before_approval: bool
    workspace_unchanged_before_approval: bool
    room_workspace_isolated: bool
    runtime_mode: str
    runtime_workspace: str
    requester: str
    approver: str
    handoff_lines: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def run_external_coding_agent_harness(
    *,
    provider: str = "codex",
    base_dir: Path | None = None,
    keep_workspace: bool = False,
) -> ExternalCodingAgentHarnessReport:
    if provider not in {"claude", "codex", "local"}:
        raise ExternalCodingAgentHarnessFailure(f"unsupported harness provider: {provider}")
    if base_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix=f"voiceops-external-agent-{provider}-"))
        cleanup = not keep_workspace
    else:
        temp_dir = base_dir / provider
        temp_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False

    try:
        workspace = temp_dir / "workspace"
        room_workspace = temp_dir / "room-workspace"
        _write_workspace(workspace)
        _write_workspace(room_workspace)
        _init_git(workspace)
        _init_git(room_workspace)

        with _isolated_client(temp_dir, workspace) as client:
            alice = _token(client, ALICE_EMAIL, ALICE_PASSWORD)
            bob = _token(client, BOB_EMAIL, BOB_PASSWORD)
            _join_room(client, alice)
            _join_room(client, bob)
            _bind_room_workspace(client, bob, room_workspace)
            _connect_provider_cli(client, alice, provider)

            original_app = (workspace / "app.py").read_text(encoding="utf-8")
            original_room_app = (room_workspace / "app.py").read_text(encoding="utf-8")
            assignment = _assign_provider(client, alice, provider)
            dispatched = _dispatch_assignment(client, alice, assignment["id"])
            action_id = dispatched["action_id"]
            _assert_truthy(action_id, "external assignment should create a pending action")
            action = _action_by_id(client, bob, action_id)
            _assert_equal(action["status"], "pending_approval", "external patch should require approval")
            _assert_equal(action["requested_by_name"], "Priya Nair", "requester should be preserved")
            _assert_contains(action["approval"]["diff"], "@app.get(\"/ready\")", "diff should include CLI-produced patch")
            _assert_equal(
                (room_workspace / "app.py").read_text(encoding="utf-8"),
                original_room_app,
                "workspace must be unchanged before approval",
            )
            _assert_equal(
                (workspace / "app.py").read_text(encoding="utf-8"),
                original_app,
                "global workspace must remain unchanged before approval",
            )

            approved = _approve_action(client, bob, action_id)
            _assert_equal(approved["status"], "completed", "approved external patch should complete")
            approval = approved["approval"]
            git = approval["git"]
            _assert_contains(git["branch_name"], f"voiceops/{action_id}-", "approval should create action branch")
            _assert_contains(
                (room_workspace / "app.py").read_text(encoding="utf-8"),
                '@app.get("/ready")',
                "approved patch should write the CLI proposal to the room workspace",
            )
            _assert_equal(
                (workspace / "app.py").read_text(encoding="utf-8"),
                original_app,
                "approved room patch must not modify the global workspace",
            )
            _assert_contains(
                (approved.get("command_output") or "").lower(),
                "passed",
                "approval should run passing tests",
            )
            handoff = _handoff(client, bob)
            _assert_handoff_contains(handoff, "Approved by Sam Ortiz")
            _assert_handoff_contains(handoff, "Branch: voiceops/")

            return ExternalCodingAgentHarnessReport(
                room_id=ROOM_ID,
                workspace_path=str(room_workspace),
                global_workspace_path=str(workspace),
                provider=provider,
                assignment_id=assignment["id"],
                action_id=action_id,
                branch=git["branch_name"],
                files_changed=git["files_changed"],
                tests_passed="passed" in (approved.get("command_output") or "").lower(),
                pending_before_approval=action["status"] == "pending_approval",
                workspace_unchanged_before_approval=True,
                room_workspace_isolated=True,
                runtime_mode=action["approval"]["runtime"]["execution_mode"],
                runtime_workspace=action["approval"]["runtime"]["workspace"],
                requester=action["requested_by_name"],
                approver=approval["decided_by_name"],
                handoff_lines=handoff["lines"],
            )
    except AssertionError:
        raise
    except Exception as exc:  # pragma: no cover - keeps manual smoke output readable.
        raise ExternalCodingAgentHarnessFailure(f"external coding agent harness setup failed: {exc}") from exc
    finally:
        if cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def print_harness_report(report: ExternalCodingAgentHarnessReport, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return
    print("External coding agent harness passed")
    print(f"Room: {report.room_id}")
    print(f"Workspace: {report.workspace_path}")
    print(f"Global workspace unchanged: {report.room_workspace_isolated}")
    print(f"Provider: {report.provider}")
    print(f"Assignment: {report.assignment_id}")
    print(f"Action: {report.action_id}")
    print(f"Branch: {report.branch}")
    print(f"Runtime: {report.runtime_mode} workspace={report.runtime_workspace}")
    print(f"Files changed: {', '.join(report.files_changed)}")
    print(f"Tests passed: {report.tests_passed}")
    print(f"Requester: {report.requester}")
    print(f"Approver: {report.approver}")
    print("Handoff highlights:")
    for line in report.handoff_lines[:6]:
        print(f"  - {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the external coding agent closure smoke harness.")
    parser.add_argument("--provider", choices=["claude", "codex", "local"], default="codex", help="External provider to exercise.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temp workspace after the run.")
    args = parser.parse_args(argv)
    report = run_external_coding_agent_harness(provider=args.provider, keep_workspace=args.keep_workspace)
    print_harness_report(report, as_json=args.json)
    return 0


@contextmanager
def _isolated_client(temp_dir: Path, workspace: Path) -> Iterator[TestClient]:
    users = UserStore(temp_dir / "users.json")
    collab = CollaborationService(CollaborationStore(temp_dir / "collab.json"))
    settings = Settings(
        users_store_path=temp_dir / "users.json",
        jwt_secret="external-coding-agent-harness-secret",
        collab_store_path=temp_dir / "collab.json",
        memory_store_path=str(temp_dir / "memory.json"),
        external_agent_store_path=temp_dir / "external-agents.json",
        external_agent_credential_secret="credential-secret-for-tests",
        agent_runs_path=temp_dir / "agent-runs.json",
        voiceops_cache_path=temp_dir / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        voiceops_workspace=str(workspace),
        external_agent_cli_execution_enabled=True,
    )
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)
    events = RoomEventHub()

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_room_event_hub] = lambda: events
    try:
        with TestClient(app) as client:
            yield client
    finally:
        voice_router._pipeline = None
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def _write_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "app.py").write_text(
        textwrap.dedent(
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/")
            def root():
                return {"service": "external-agent-harness"}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    _write_patch_cli(path / "claude_patch.py", label="claude code")
    _write_patch_cli(path / "codex_patch.py", label="codex")
    _write_patch_cli(path / "local_patch.py", label="local open agent")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        textwrap.dedent(
            """
            from fastapi.testclient import TestClient
            from app import app

            client = TestClient(app)

            def test_ready():
                response = client.get("/ready")
                assert response.status_code == 200
                assert response.json()["status"] == "ready"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (path / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "VoiceOps Tests"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


def _token(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    _assert_equal(response.status_code, 200, f"login should work for {email}")
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _join_room(client: TestClient, token: str) -> None:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/join",
        headers=_auth(token),
        json={"room_name": "External agent room", "project": "workspace"},
    )
    _assert_equal(response.status_code, 200, "participant should join the room")


def _bind_room_workspace(client: TestClient, token: str, workspace: Path) -> None:
    response = client.put(
        f"/collab/rooms/{ROOM_ID}/workspace",
        headers=_auth(token),
        json={"path": str(workspace)},
    )
    _assert_equal(response.status_code, 200, "admin should bind the room workspace")


def _write_patch_cli(path: Path, *, label: str) -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            path = Path("app.py")
            source = path.read_text(encoding="utf-8")
            if '@app.get("/ready")' not in source:
                path.write_text(
                    source.rstrip()
                    + '\\n\\n@app.get("/ready")\\ndef ready() -> dict[str, str]:\\n    return {{"status": "ready"}}\\n',
                    encoding="utf-8",
                )
            print("{label} local cli prepared /ready")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _connect_provider_cli(client: TestClient, token: str, provider: str) -> None:
    response = client.post(
        f"/external-agents/providers/{provider}/credentials/local-cli",
        headers=_auth(token),
        json={"command": f"python {provider}_patch.py", "account_label": f"{_provider_label(provider)} local CLI"},
    )
    _assert_equal(response.status_code, 200, f"{provider} local CLI credential should connect")


def _assign_provider(client: TestClient, token: str, provider: str) -> dict:
    response = client.post(
        f"/agents/rooms/{ROOM_ID}/assignments",
        headers=_auth(token),
        json={
            "agent_id": provider,
            "agent_label": _provider_label(provider),
            "agent_kind": "external",
            "task": "add the readiness endpoint",
            "mode": "patch",
        },
    )
    _assert_equal(response.status_code, 200, f"{provider} assignment should be created")
    return response.json()


def _provider_label(provider: str) -> str:
    if provider == "local":
        return "Local Open Agent"
    if provider == "claude":
        return "Claude Code"
    return "Codex Agent"


def _dispatch_assignment(client: TestClient, token: str, assignment_id: str) -> dict:
    response = client.post(
        f"/agents/rooms/{ROOM_ID}/assignments/{assignment_id}/dispatch",
        headers=_auth(token),
    )
    _assert_equal(response.status_code, 200, "external assignment should dispatch")
    data = response.json()
    _assert_equal(data["status"], "completed", "external assignment should finish after creating pending patch")
    _assert_equal(data["metadata"]["provider_status"], "pending_approval", "provider run should wait for approval")
    return data


def _approve_action(client: TestClient, token: str, action_id: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/actions/{action_id}/approve",
        headers=_auth(token),
        json={"note": "approved by harness"},
    )
    _assert_equal(response.status_code, 200, "Bob should approve the external agent patch")
    return response.json()


def _action_by_id(client: TestClient, token: str, action_id: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}", headers=_auth(token))
    _assert_equal(response.status_code, 200, "room snapshot should be available")
    for action in response.json()["actions"]:
        if action["id"] == action_id:
            return action
    raise ExternalCodingAgentHarnessFailure(f"action not found: {action_id}")


def _handoff(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}/handoff", headers=_auth(token))
    _assert_equal(response.status_code, 200, "handoff should be available")
    return response.json()


def _assert_handoff_contains(handoff: dict, expected: str) -> None:
    text = "\n".join(handoff.get("lines") or [])
    _assert_contains(text, expected, f"handoff should contain {expected!r}")


def _assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise ExternalCodingAgentHarnessFailure(f"{message}: expected {expected!r}, got {actual!r}")


def _assert_truthy(value, message: str) -> None:
    if not value:
        raise ExternalCodingAgentHarnessFailure(message)


def _assert_contains(text: str, expected: str, message: str) -> None:
    if expected not in text:
        raise ExternalCodingAgentHarnessFailure(f"{message}: missing {expected!r} in {text!r}")


if __name__ == "__main__":
    raise SystemExit(main())
