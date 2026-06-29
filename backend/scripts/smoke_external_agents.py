from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
import app.voice_agent.router as voice_router


@dataclass
class ExternalAgentSmokeReport:
    provider: str
    auth_method: str
    provider_run_id: str
    action_id: str
    status: str
    preflight_status: str
    provider_preflight_state: str
    provider_preflight_summary: str
    provider_preflight_blockers: list[str]
    runtime_evidence: dict[str, object]
    setup_guide_state: str
    setup_guide_path: str
    setup_guide_next_step: str
    setup_guide_recommended_steps: list[str]
    files_changed: list[str]
    workspace_unchanged_before_approval: bool
    diff_preview_present: bool
    token_redacted: bool


def run_smoke(*, keep_workspace: bool = False) -> ExternalAgentSmokeReport:
    temp_dir = Path(tempfile.mkdtemp(prefix="voiceops-external-agent-"))
    cleanup = not keep_workspace
    try:
        workspace = temp_dir / "workspace"
        workspace.mkdir()
        (workspace / "app.py").write_text(
            textwrap.dedent(
                """
                from fastapi import FastAPI

                app = FastAPI()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        original = (workspace / "app.py").read_text(encoding="utf-8")
        settings = Settings(
            users_store_path=temp_dir / "users.json",
            jwt_secret="external-agent-smoke-secret",
            external_agent_store_path=temp_dir / "external-agents.json",
            external_agent_credential_secret="credential-secret-for-smoke-tests",
            collab_store_path=temp_dir / "collab-unused.json",
            memory_store_path=str(temp_dir / "memory.json"),
            voiceops_workspace=str(workspace),
        )
        users = UserStore(settings.users_store_path)
        collab = CollaborationService(CollaborationStore(temp_dir / "collab.json"))
        old_overrides = dict(app.dependency_overrides)
        voice_router._pipeline = None
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_user_store] = lambda: users
        app.dependency_overrides[get_collaboration_service] = lambda: collab
        app.dependency_overrides[get_room_event_hub] = lambda: RoomEventHub()
        try:
            with TestClient(app) as client:
                token = _token(client)
                headers = {"Authorization": f"Bearer {token}"}
                _assert(client.post("/collab/rooms/main/join", headers=headers, json={}).status_code == 200, "join failed")
                connected = client.post(
                    "/external-agents/providers/cursor/credentials/local-cli",
                    headers=headers,
                    json={"account_label": "Cursor local smoke"},
                )
                _assert(connected.status_code == 200, connected.text)
                preflight = client.get("/external-agents/preflight", headers=headers)
                _assert(preflight.status_code == 200, preflight.text)
                preflight_body = preflight.json()
                cursor_preflight = next(
                    item for item in preflight_body["providers"]
                    if item["provider"] == "cursor"
                )
                setup = client.get("/external-agents/setup-guide", headers=headers)
                _assert(setup.status_code == 200, setup.text)
                cursor_setup = next(
                    item for item in setup.json()["providers"]
                    if item["provider"] == "cursor"
                )
                run = client.post(
                    "/external-agents/rooms/main/runs",
                    headers=headers,
                    json={"provider": "cursor", "mode": "patch", "prompt": "fix the health endpoint"},
                )
                _assert(run.status_code == 200, run.text)
                result = run.json()
                room = client.get("/collab/rooms/main", headers=headers).json()
                action = next(item for item in room["actions"] if item["id"] == result["action_id"])
                unchanged = (workspace / "app.py").read_text(encoding="utf-8") == original
                diff_preview = "--- a/app.py" in (result.get("diff") or "")
                token_redacted = "credential-secret" not in settings.external_agent_store_path.read_text(encoding="utf-8")
                _assert(action["status"] == "pending_approval", "action is not pending approval")
                _assert(unchanged, "workspace changed before approval")
                _assert(diff_preview, "diff preview missing")
                _assert(cursor_setup["recommended_path"] == "local_cli_for_code_changes", "setup guide did not recommend local CLI for code changes")
                return ExternalAgentSmokeReport(
                    provider=result["provider"],
                    auth_method=connected.json()["auth_method"],
                    provider_run_id=result["provider_run_id"],
                    action_id=result["action_id"],
                    status=result["status"],
                    preflight_status=preflight_body["status"],
                    provider_preflight_state=cursor_preflight["state"],
                    provider_preflight_summary=cursor_preflight["summary"],
                    provider_preflight_blockers=cursor_preflight["blockers"],
                    runtime_evidence=cursor_preflight["evidence"],
                    setup_guide_state=cursor_setup["state"],
                    setup_guide_path=cursor_setup["recommended_path"],
                    setup_guide_next_step=cursor_setup["next_step"],
                    setup_guide_recommended_steps=[
                        step["id"]
                        for step in cursor_setup["steps"]
                        if step.get("recommended")
                    ],
                    files_changed=result["files_changed"],
                    workspace_unchanged_before_approval=unchanged,
                    diff_preview_present=diff_preview,
                    token_redacted=token_redacted,
                )
        finally:
            voice_router._pipeline = None
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
    finally:
        if cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _token(client: TestClient) -> str:
    response = client.post("/auth/login", json={"email": "priya@voiceops.dev", "password": "oncall123"})
    _assert(response.status_code == 200, response.text)
    return response.json()["access_token"]


def _assert(condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the external agent provider smoke test.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep temporary workspace.")
    args = parser.parse_args(argv)
    try:
        report = run_smoke(keep_workspace=args.keep_workspace)
    except AssertionError as exc:
        print(f"External agent smoke failed: {exc}")
        return 1
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print("External agent smoke passed")
        print(f"Provider: {report.provider}")
        print(f"Run: {report.provider_run_id}")
        print(f"Action: {report.action_id}")
        print(f"Status: {report.status}")
        print(f"Preflight: {report.provider_preflight_state} ({report.provider_preflight_summary})")
        print(f"Setup guide: {report.setup_guide_path} ({report.setup_guide_next_step})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
