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
from app.main import app
from app.speakers.provider import MockSpeakerProvider
from app.speakers.service import SpeakerService, get_speaker_service
from app.speakers.store import SpeakerStore
import app.collab.router as collab_router
import app.voice_agent.router as voice_router


ROOM_ID = "main"
ALICE_EMAIL = "priya@voiceops.dev"
ALICE_PASSWORD = "oncall123"
BOB_EMAIL = "admin@voiceops.dev"
BOB_PASSWORD = "admin123"


class MultiAgentHarnessFailure(AssertionError):
    """Raised when the deterministic multi-agent closure flow breaks."""


@dataclass
class MultiAgentHarnessReport:
    room_id: str
    workspace_path: str
    run_id: str
    route: str
    roles: list[str]
    exchange_count: int
    revision_exchange_seen: bool
    control_budget: int
    control_timeout_seconds: float
    action_id: str
    plan_approval_required: bool
    approval_policy_mode: str
    approval_llm_provider: str
    approval_llm_fallback: bool
    approval_multi_agent_run_id: str
    dashboard_pending_approval_count: int
    dashboard_completed_patch_count: int
    dashboard_pending_ready_state: str
    dashboard_completed_ready_state: str
    dashboard_branch: str
    dashboard_approver: str
    revision_count: int
    preapproval_tests_passed: bool
    branch: str
    tests_passed: bool
    handoff_lines: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def run_multi_agent_harness(
    *,
    base_dir: Path | None = None,
    keep_workspace: bool = False,
) -> MultiAgentHarnessReport:
    if base_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="voiceops-multi-agent-"))
        cleanup = not keep_workspace
    else:
        temp_dir = base_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False

    try:
        workspace = temp_dir / "workspace"
        _write_workspace(workspace)
        _init_git(workspace)

        with _isolated_client(temp_dir, workspace) as client:
            alice = _token(client, ALICE_EMAIL, ALICE_PASSWORD)
            bob = _token(client, BOB_EMAIL, BOB_PASSWORD)
            _join(client, alice)
            _join(client, bob)
            _seed_task(client, alice)

            original = (workspace / "app.py").read_text(encoding="utf-8")
            run = _start_run(client, alice)
            action_id = run["action_id"]
            _assert_equal(run["route"], "meeting_patch_closure", "Coordinator should select patch closure")
            _assert_equal(
                [step["role"] for step in run["steps"]],
                ["coordinator", "meeting", "memory", "code", "review", "code", "review", "test", "git"],
                "Run should include the review-requested revision loop",
            )
            _assert_equal(
                len([step for step in run["steps"] if step["role"] == "code"]),
                2,
                "Code Agent should revise after Review Agent asks for changes",
            )
            exchange_pairs = [
                (exchange["from_role"], exchange["to_role"], exchange["title"])
                for exchange in run.get("exchanges", [])
            ]
            _assert_equal(
                len(run.get("exchanges", [])) >= 7,
                True,
                "Agent run should record specialist handoffs as exchanges",
            )
            _assert_equal(
                ("review", "code", "Revision requested") in exchange_pairs,
                True,
                "Review Agent should record the revision request handoff to Code Agent",
            )
            _assert_equal(
                (workspace / "app.py").read_text(encoding="utf-8"),
                original,
                "Agent run should not write before approval",
            )
            pending_action = _action(client, alice, action_id)
            pending_approval = pending_action["approval"]
            _assert_equal(
                pending_approval["policy"]["mode"],
                "preview_first",
                "Pending action should record preview-first policy",
            )
            _assert_equal(
                pending_approval["policy"]["workspace_write_before_approval"],
                False,
                "Pending action should record no workspace write before approval",
            )
            _assert_equal(
                pending_approval["multi_agent"]["run_id"],
                run["id"],
                "Pending action should link back to the multi-agent run",
            )
            _assert_equal(
                pending_approval["multi_agent"]["plan"]["metadata"]["approval_required"],
                True,
                "Pending action should preserve approval-required plan metadata",
            )
            _assert_equal(
                pending_approval["llm"]["provider"],
                "mock",
                "Pending action should record the LLM provider used for patch generation",
            )
            dashboard_pending = _dashboard(client, alice)
            pending_metrics = _dashboard_metrics(dashboard_pending)
            pending_queue = _dashboard_queue(dashboard_pending)
            pending_dashboard_action = _dashboard_action_item(dashboard_pending["approvals"], action_id)
            _assert_equal(
                pending_metrics["pending approvals"],
                1,
                "Work dashboard should expose the pending multi-agent patch approval",
            )
            _assert_equal(
                pending_queue["Approvals"]["value"],
                1,
                "Work dashboard queue health should count the pending approval",
            )
            _assert_equal(
                pending_dashboard_action["status"],
                "pending_approval",
                "Work dashboard approval item should remain pending before Bob approves",
            )
            _assert_equal(
                pending_dashboard_action["metadata"]["pending_approval"],
                True,
                "Work dashboard approval item should preserve pending approval metadata",
            )
            _assert_equal(
                pending_dashboard_action["metadata"]["branch"],
                None,
                "Work dashboard should not claim a git branch before approval",
            )
            _assert_equal(
                dashboard_pending["readiness"]["state"],
                "needs_attention",
                "Work dashboard should block readiness while approval is waiting",
            )
            _assert_equal(
                any("patch approval" in item for item in dashboard_pending["readiness"]["blockers"]),
                True,
                "Work dashboard readiness should explain the approval blocker",
            )

            approved = _approve(client, bob, action_id)
            approval = approved["approval"]
            _assert_equal(
                approval["preapproval_test"]["passed"],
                True,
                "Test Agent should validate the proposal in a temporary workspace before approval",
            )
            branch = approval["git"]["branch_name"]
            output = approved.get("command_output") or ""
            _assert_equal(approved["status"], "completed", "Approved action should complete")
            _assert_contains(branch, f"voiceops/{action_id}-", "Git Agent should create action branch on approval")
            _assert_contains(output.lower(), "passed", "Test Agent plan should pass on approval")
            _assert_contains(
                (workspace / "app.py").read_text(encoding="utf-8"),
                '@app.get("/health")',
                "Approved patch should add health route",
            )
            dashboard_completed = _dashboard(client, bob)
            completed_metrics = _dashboard_metrics(dashboard_completed)
            completed_queue = _dashboard_queue(dashboard_completed)
            completed_dashboard_action = _dashboard_action_item(dashboard_completed["recent_actions"], action_id)
            _assert_equal(
                completed_metrics["pending approvals"],
                0,
                "Work dashboard should clear pending approvals after Bob approves",
            )
            _assert_equal(
                completed_metrics["completed patches"],
                1,
                "Work dashboard should count the approved multi-agent patch as completed",
            )
            _assert_equal(
                completed_queue["Approvals"]["value"],
                0,
                "Work dashboard queue health should clear approval count after approval",
            )
            _assert_equal(
                completed_dashboard_action["status"],
                "completed",
                "Work dashboard recent action should show the approved patch as completed",
            )
            _assert_equal(
                completed_dashboard_action["metadata"]["approver"],
                "Sam Ortiz",
                "Work dashboard recent action should expose the human approver",
            )
            _assert_equal(
                completed_dashboard_action["metadata"]["branch"],
                branch,
                "Work dashboard recent action should expose the action branch",
            )
            _assert_contains(
                completed_dashboard_action.get("detail") or "",
                branch,
                "Work dashboard recent action detail should include branch",
            )
            _assert_equal(
                dashboard_completed["readiness"]["state"],
                "ready",
                "Work dashboard should be ready after the multi-agent patch is approved",
            )

            handoff = _handoff(client, bob)
            _assert_handoff_contains(handoff, "Approved by Sam Ortiz")
            _assert_handoff_contains(handoff, "Branch: voiceops/")
            return MultiAgentHarnessReport(
                room_id=ROOM_ID,
                workspace_path=str(workspace),
                run_id=run["id"],
                route=run["route"],
                roles=[step["role"] for step in run["steps"]],
                exchange_count=len(run.get("exchanges", [])),
                revision_exchange_seen=("review", "code", "Revision requested") in exchange_pairs,
                control_budget=run["metadata"]["control"]["max_steps"],
                control_timeout_seconds=run["metadata"]["control"]["timeout_seconds"],
                action_id=action_id,
                plan_approval_required=pending_approval["multi_agent"]["plan"]["metadata"]["approval_required"],
                approval_policy_mode=pending_approval["policy"]["mode"],
                approval_llm_provider=pending_approval["llm"]["provider"],
                approval_llm_fallback=bool(pending_approval["llm"]["fallback"]),
                approval_multi_agent_run_id=pending_approval["multi_agent"]["run_id"],
                dashboard_pending_approval_count=pending_metrics["pending approvals"],
                dashboard_completed_patch_count=completed_metrics["completed patches"],
                dashboard_pending_ready_state=dashboard_pending["readiness"]["state"],
                dashboard_completed_ready_state=dashboard_completed["readiness"]["state"],
                dashboard_branch=completed_dashboard_action["metadata"]["branch"],
                dashboard_approver=completed_dashboard_action["metadata"]["approver"],
                revision_count=approval.get("revision_count") or 0,
                preapproval_tests_passed=approval["preapproval_test"]["passed"],
                branch=branch,
                tests_passed="passed" in output.lower(),
                handoff_lines=handoff["lines"],
            )
    except AssertionError:
        raise
    except Exception as exc:  # pragma: no cover
        raise MultiAgentHarnessFailure(f"multi-agent harness setup failed: {exc}") from exc
    finally:
        if cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def print_multi_agent_report(report: MultiAgentHarnessReport, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return
    print("Multi-agent harness passed")
    print(f"Run: {report.run_id}")
    print(f"Route: {report.route}")
    print(f"Roles: {', '.join(report.roles)}")
    print(f"Exchanges: {report.exchange_count}")
    print(f"Revision exchange seen: {report.revision_exchange_seen}")
    print(f"Control: budget={report.control_budget} timeout={report.control_timeout_seconds}s")
    print(f"Action: {report.action_id}")
    print(f"Plan approval required: {report.plan_approval_required}")
    print(f"Approval policy: {report.approval_policy_mode}")
    print(f"Patch LLM provider: {report.approval_llm_provider} fallback={report.approval_llm_fallback}")
    print(
        "Work dashboard: "
        f"pending={report.dashboard_pending_approval_count} "
        f"completed={report.dashboard_completed_patch_count} "
        f"ready={report.dashboard_completed_ready_state}"
    )
    print(f"Dashboard approver: {report.dashboard_approver}")
    print(f"Dashboard branch: {report.dashboard_branch}")
    print(f"Revisions: {report.revision_count}")
    print(f"Pre-approval tests passed: {report.preapproval_tests_passed}")
    print(f"Branch: {report.branch}")
    print(f"Tests passed: {report.tests_passed}")
    print("Handoff highlights:")
    for line in report.handoff_lines[:6]:
        print(f"  - {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic multi-agent closure harness.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep temporary workspace.")
    args = parser.parse_args(argv)
    try:
        report = run_multi_agent_harness(keep_workspace=args.keep_workspace)
    except MultiAgentHarnessFailure as exc:
        print(f"Multi-agent harness failed: {exc}")
        return 1
    print_multi_agent_report(report, as_json=args.json)
    return 0


@contextmanager
def _isolated_client(temp_dir: Path, workspace: Path) -> Iterator[TestClient]:
    users_path = temp_dir / "users.json"
    settings = Settings(
        users_store_path=users_path,
        jwt_secret="multi-agent-harness-secret",
        collab_store_path=temp_dir / "collab-unused.json",
        memory_store_path=str(temp_dir / "memory.json"),
        speaker_store_path=temp_dir / "speakers-unused.json",
        agent_runs_path=temp_dir / "agent-runs.json",
        rag_index_path=temp_dir / "rag-index.json",
        voiceops_cache_path=temp_dir / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        speaker_provider="mock",
        voiceops_workspace=str(workspace),
    )
    users = UserStore(users_path)
    collab = CollaborationService(CollaborationStore(temp_dir / "collab.json"))
    speakers = SpeakerService(SpeakerStore(temp_dir / "speakers.json"), collab, MockSpeakerProvider())
    agents = MultiAgentService(AgentRunStore(temp_dir / "agent-runs.json"), collab, settings)
    events = RoomEventHub()

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_speaker_service] = lambda: speakers
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[collab_router.get_dashboard_multi_agent_service] = lambda: agents
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

            # GET /health is intentionally missing
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (path / "test_app.py").write_text(
        textwrap.dedent(
            """
            from app import app


            def test_health_route_exists():
                routes = [route.path for route in app.routes]
                assert "/health" in routes
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial", "-q"], cwd=path, check=True)


def _token(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    _assert_equal(response.status_code, 200, f"login should work for {email}")
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _join(client: TestClient, token: str) -> None:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/join",
        headers=_auth(token),
        json={"room_name": "Team room", "project": "workspace"},
    )
    _assert_equal(response.status_code, 200, "join should succeed")


def _seed_task(client: TestClient, token: str) -> None:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/messages",
        headers=_auth(token),
        json={
            "text": "Open task: fix the missing health check in app.py and include a review note",
            "source": "meeting_audio",
        },
    )
    _assert_equal(response.status_code, 200, "meeting task should be recorded")


def _start_run(client: TestClient, token: str) -> dict:
    response = client.post(
        f"/agents/rooms/{ROOM_ID}/runs",
        headers=_auth(token),
        json={"prompt": "AI fix that", "source": "meeting"},
    )
    _assert_equal(response.status_code, 200, "multi-agent run should start")
    return response.json()


def _approve(client: TestClient, token: str, action_id: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/actions/{action_id}/approve",
        headers=_auth(token),
        json={"note": "approved by harness"},
    )
    _assert_equal(response.status_code, 200, "approval should succeed")
    return response.json()


def _action(client: TestClient, token: str, action_id: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}", headers=_auth(token))
    _assert_equal(response.status_code, 200, "room snapshot should load")
    for action in response.json()["actions"]:
        if action["id"] == action_id:
            return action
    raise MultiAgentHarnessFailure(f"action {action_id!r} should exist in room snapshot")


def _dashboard(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}/work-dashboard", headers=_auth(token))
    _assert_equal(response.status_code, 200, "work dashboard should load")
    return response.json()


def _dashboard_metrics(dashboard: dict) -> dict[str, int]:
    return {item["label"]: item["value"] for item in dashboard.get("metrics", [])}


def _dashboard_queue(dashboard: dict) -> dict[str, dict]:
    return {item["label"]: item for item in dashboard.get("queue_health", [])}


def _dashboard_action_item(items: list[dict], action_id: str) -> dict:
    for item in items:
        if item.get("action_id") == action_id or item.get("id") == action_id:
            return item
    raise MultiAgentHarnessFailure(f"dashboard should include action {action_id!r}; got {items!r}")


def _handoff(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}/handoff", headers=_auth(token))
    _assert_equal(response.status_code, 200, "handoff should load")
    return response.json()


def _assert_handoff_contains(handoff: dict, expected: str) -> None:
    if not any(expected in line for line in handoff.get("lines", [])):
        raise MultiAgentHarnessFailure(f"handoff should contain {expected!r}; got {handoff.get('lines', [])!r}")


def _assert_contains(actual: str, expected: str, message: str) -> None:
    if expected not in actual:
        raise MultiAgentHarnessFailure(f"{message}: expected {expected!r} in {actual!r}")


def _assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise MultiAgentHarnessFailure(f"{message}: expected {expected!r}, got {actual!r}")


if __name__ == "__main__":
    raise SystemExit(main())
