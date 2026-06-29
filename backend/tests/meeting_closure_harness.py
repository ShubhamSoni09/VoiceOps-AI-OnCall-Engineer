from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import textwrap
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

from app.agents.service import MultiAgentService, get_multi_agent_service
from app.agents.store import AgentRunStore
from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.sqlite_store import SQLiteCollaborationStore
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.external_agents.service import ExternalAgentService
from app.external_agents.store import ExternalAgentCredentialStore
from app.main import app
from app.speakers.provider import MockSpeakerProvider
from app.speakers.service import SpeakerService, get_speaker_service
from app.speakers.store import SpeakerStore
import app.voice_agent.router as voice_router


ROOM_ID = "main"
ALICE_EMAIL = "priya@voiceops.dev"
ALICE_PASSWORD = "oncall123"
BOB_EMAIL = "admin@voiceops.dev"
BOB_PASSWORD = "admin123"


class HarnessFailure(AssertionError):
    """Raised when the meeting closure smoke flow breaks at a named step."""


@dataclass
class HarnessReport:
    room_id: str
    workspace_path: str
    collab_backend: str
    event_store_ready: bool
    event_count: int
    event_types: dict[str, int]
    alice: str
    bob: str
    speaker_mappings: dict[str, str]
    memory_answer: str
    external_assignment_id: str | None
    external_action_id: str | None
    external_provider: str | None
    external_auto_dispatched: bool
    external_pending_seen_by_bob: bool
    action_id: str
    pending_seen_by_alice: bool
    pending_seen_by_bob: bool
    completed_seen_by_alice: bool
    completed_seen_by_bob: bool
    branch: str
    files_changed: list[str]
    tests_passed: bool
    git_dirty_after: bool
    trace_event_count: int
    trace_coverage: dict[str, bool]
    trace_missing: list[str]
    handoff_lines: list[str]
    rejected_action_id: str | None = None
    rejected_handoff_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PullRequestRoomHarnessReport:
    room_id: str
    global_workspace_path: str
    room_workspace_path: str
    action_id: str
    branch: str
    commit_sha: str
    remote_url: str
    web_url: str
    workspace_source: str
    workspace_path: str
    ready: bool
    global_workspace_unchanged: bool

    def to_dict(self) -> dict:
        return asdict(self)


def run_meeting_closure_harness(
    *,
    base_dir: Path | None = None,
    keep_workspace: bool = False,
    include_reject_path: bool = True,
    collab_backend: str = "json",
) -> HarnessReport:
    """Run the deterministic two-person meeting closure flow in an isolated app."""

    if base_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="voiceops-meeting-closure-"))
        cleanup = not keep_workspace
    else:
        temp_dir = base_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False

    try:
        workspace = temp_dir / "workspace"
        _write_workspace(workspace)
        _init_git(workspace)

        with _isolated_client(temp_dir, workspace, collab_backend=collab_backend) as client:
            alice_token = _token(client, ALICE_EMAIL, ALICE_PASSWORD)
            bob_token = _token(client, BOB_EMAIL, BOB_PASSWORD)

            _join_room(client, alice_token)
            _join_room(client, bob_token)
            speaker_mappings = _map_speakers(client, bob_token)
            _ingest_meeting_segments(client, alice_token)

            memory_answer = _query_memory(client, bob_token)
            _assert_contains(
                memory_answer.lower(),
                "health check",
                "memory query should include the meeting task",
            )

            original_app = (workspace / "app.py").read_text(encoding="utf-8")
            rejected_action_id: str | None = None
            rejected_handoff_lines: list[str] = []

            _connect_local_external_agent(client, alice_token)
            external_assignment, external_action = _request_external_auto_patch_from_meeting_context(client, alice_token)
            external_assignment_id = external_assignment["id"]
            external_action_id = external_action["id"]
            external_provider = external_action["approval"]["external_agent"]["provider"]
            _assert_equal(external_assignment["status"], "completed", "external assignment should dispatch immediately")
            _assert_truthy(
                external_assignment["metadata"]["recommended_provider"] == "local",
                "external auto assignment should recommend the local provider",
            )
            _assert_equal(external_action["status"], "pending_approval", "external patch should wait for approval")
            _assert_equal(external_provider, "local", "external patch should be proposed by local provider")
            external_pending_seen_by_bob = _action_has_status(
                client,
                bob_token,
                external_action_id,
                "pending_approval",
            )
            _assert_truthy(external_pending_seen_by_bob, "Bob should see the external pending patch")
            _assert_equal(
                (workspace / "app.py").read_text(encoding="utf-8"),
                original_app,
                "external pending patch must not edit the workspace before approval",
            )
            _reject_patch(client, bob_token, external_action_id, workspace, original_app)

            action = _request_patch_from_meeting_context(client, alice_token)
            action_id = action["id"]
            _assert_equal(action["status"], "pending_approval", "patch should wait for approval")
            _assert_equal(action["requested_by_name"], "Priya Nair", "patch requester should be Alice/Priya")
            pending_seen_by_alice = _action_has_status(client, alice_token, action_id, "pending_approval")
            pending_seen_by_bob = _action_has_status(client, bob_token, action_id, "pending_approval")
            _assert_truthy(pending_seen_by_alice, "Alice should see her pending patch")
            _assert_truthy(pending_seen_by_bob, "Bob should see Alice's pending patch before approval")
            _assert_contains(
                action["approval"]["diff"],
                "--- a/app.py",
                "pending patch should expose a unified diff",
            )
            _assert_equal(
                (workspace / "app.py").read_text(encoding="utf-8"),
                original_app,
                "pending patch must not edit the workspace",
            )

            if include_reject_path:
                rejected_action_id, rejected_handoff_lines = _reject_patch(
                    client,
                    bob_token,
                    action_id,
                    workspace,
                    original_app,
                )
                action = _request_patch_from_meeting_context(client, alice_token)
                action_id = action["id"]
                _assert_equal(action["status"], "pending_approval", "second patch should wait for approval")
                pending_seen_by_alice = _action_has_status(client, alice_token, action_id, "pending_approval")
                pending_seen_by_bob = _action_has_status(client, bob_token, action_id, "pending_approval")
                _assert_truthy(pending_seen_by_alice, "Alice should see the regenerated pending patch")
                _assert_truthy(pending_seen_by_bob, "Bob should see the regenerated pending patch")
                _assert_equal(
                    (workspace / "app.py").read_text(encoding="utf-8"),
                    original_app,
                    "workspace should still be unchanged after regenerating a pending patch",
                )

            approved = _approve_patch(client, bob_token, action_id)
            approval = approved["approval"]
            git = approval["git"]
            command_output = approved.get("command_output") or ""
            _assert_equal(approved["status"], "completed", "approved patch should complete")
            _assert_equal(approval["decided_by_name"], "Sam Ortiz", "approver should be Bob/Sam")
            _assert_contains(git["branch_name"], f"voiceops/{action_id}-", "approval should create an action branch")
            _assert_contains(
                (workspace / "app.py").read_text(encoding="utf-8"),
                '@app.get("/health")',
                "approved patch should add the health endpoint",
            )
            _assert_contains(command_output.lower(), "passed", "approval should run passing tests")
            completed_seen_by_alice = _action_has_status(client, alice_token, action_id, "completed")
            completed_seen_by_bob = _action_has_status(client, bob_token, action_id, "completed")
            _assert_truthy(completed_seen_by_alice, "Alice should see Bob's completed approval")
            _assert_truthy(completed_seen_by_bob, "Bob should see the completed approval")

            status = _get_git_status(client, bob_token)
            handoff = _get_handoff(client, bob_token)
            event_store = _get_event_store_readiness(client, bob_token)
            trace = _get_room_trace(client, bob_token)
            if collab_backend == "sqlite":
                _assert_truthy(event_store["ready"], "SQLite event store should be ready")
                _assert_truthy(event_store["event_count"] > 0, "SQLite event store should record collaboration events")
                _assert_truthy(
                    "message.appended" in event_store["event_types"],
                    "SQLite event log should include timeline message events",
                )
                _assert_truthy(trace["coverage"]["event_store"], "SQLite trace should include durable event-store coverage")
            _assert_handoff_contains(handoff, "Priya Nair asked VoiceOps to patch")
            _assert_handoff_contains(handoff, "Approved by Sam Ortiz")
            _assert_handoff_contains(handoff, "Branch: voiceops/")
            _assert_handoff_contains(handoff, "Route: agent pipeline (approval required before workspace write)")
            _assert_handoff_not_contains(handoff, "Priya Nair noted task: Sam Ortiz")
            _assert_trace_covers_closure(trace)

            return HarnessReport(
                room_id=ROOM_ID,
                workspace_path=str(workspace),
                collab_backend=collab_backend,
                event_store_ready=bool(event_store["ready"]),
                event_count=int(event_store["event_count"]),
                event_types=dict(event_store.get("event_types") or {}),
                alice="Priya Nair",
                bob="Sam Ortiz",
                speaker_mappings=speaker_mappings,
                memory_answer=memory_answer,
                external_assignment_id=external_assignment_id,
                external_action_id=external_action_id,
                external_provider=external_provider,
                external_auto_dispatched=True,
                external_pending_seen_by_bob=external_pending_seen_by_bob,
                action_id=action_id,
                pending_seen_by_alice=pending_seen_by_alice,
                pending_seen_by_bob=pending_seen_by_bob,
                completed_seen_by_alice=completed_seen_by_alice,
                completed_seen_by_bob=completed_seen_by_bob,
                branch=git["branch_name"],
                files_changed=git["files_changed"],
                tests_passed="passed" in command_output.lower(),
                git_dirty_after=status["dirty"],
                trace_event_count=len(trace["events"]),
                trace_coverage=dict(trace["coverage"]),
                trace_missing=list(trace.get("missing") or []),
                handoff_lines=handoff["lines"],
                rejected_action_id=rejected_action_id,
                rejected_handoff_lines=rejected_handoff_lines,
            )
    except AssertionError:
        raise
    except Exception as exc:  # pragma: no cover - keeps manual smoke output readable.
        raise HarnessFailure(f"meeting closure harness setup failed: {exc}") from exc
    finally:
        if cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def run_pull_request_room_harness(
    *,
    base_dir: Path | None = None,
    keep_workspace: bool = False,
) -> PullRequestRoomHarnessReport:
    if base_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="voiceops-room-pr-"))
        cleanup = not keep_workspace
    else:
        temp_dir = base_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False

    try:
        workspace = temp_dir / "workspace"
        room_workspace = temp_dir / "room-workspace"
        _write_workspace(workspace)
        _write_workspace(room_workspace)
        _init_git(workspace)
        _init_git(room_workspace)
        _add_origin(workspace, "https://github.com/global/app.git")
        _add_origin(room_workspace, "git@github.com:room/app.git")
        global_head = _git_head(workspace)
        global_app = (workspace / "app.py").read_text(encoding="utf-8")

        with _isolated_client(temp_dir, workspace, collab_backend="json") as client:
            alice_token = _token(client, ALICE_EMAIL, ALICE_PASSWORD)
            bob_token = _token(client, BOB_EMAIL, BOB_PASSWORD)
            _join_room(client, alice_token)
            _join_room(client, bob_token)
            _bind_room_workspace(client, bob_token, room_workspace)

            action = _request_health_patch(client, alice_token)
            approved = _approve_patch(client, bob_token, action["id"])
            committed = _commit_patch(client, bob_token, action["id"])
            plan = _plan_pull_request(client, bob_token, action["id"])

            _assert_equal(approved["status"], "completed", "room PR harness patch should approve")
            _assert_truthy(committed["approval"]["git"]["commit_sha"], "room PR harness should create a commit")
            _assert_truthy(plan["ready"], "room PR dry-run plan should be ready")
            _assert_equal(plan["remote_url"], "git@github.com:room/app.git", "PR plan should use room repo remote")
            _assert_equal(plan["web_url"], "https://github.com/room/app", "PR plan should use room repo web URL")
            _assert_equal(plan["preflight"]["workspace_source"], "room", "PR plan should report room workspace source")
            _assert_equal(
                plan["preflight"]["workspace_path"],
                str(room_workspace.resolve()),
                "PR plan should report room workspace path",
            )
            global_unchanged = _git_head(workspace) == global_head and (workspace / "app.py").read_text(encoding="utf-8") == global_app
            _assert_truthy(global_unchanged, "room PR flow should leave global workspace unchanged")

            return PullRequestRoomHarnessReport(
                room_id=ROOM_ID,
                global_workspace_path=str(workspace.resolve()),
                room_workspace_path=str(room_workspace.resolve()),
                action_id=action["id"],
                branch=committed["approval"]["git"]["branch_name"],
                commit_sha=committed["approval"]["git"]["commit_sha"],
                remote_url=plan["remote_url"],
                web_url=plan["web_url"],
                workspace_source=plan["preflight"]["workspace_source"],
                workspace_path=plan["preflight"]["workspace_path"],
                ready=plan["ready"],
                global_workspace_unchanged=global_unchanged,
            )
    except AssertionError:
        raise
    except Exception as exc:  # pragma: no cover - keeps manual smoke output readable.
        raise HarnessFailure(f"room pull-request harness setup failed: {exc}") from exc
    finally:
        if cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def print_harness_report(report: HarnessReport, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return

    print("Meeting closure harness passed")
    print(f"Room: {report.room_id}")
    print(f"Workspace: {report.workspace_path}")
    print(f"Collab backend: {report.collab_backend}")
    print(f"Event store: ready={report.event_store_ready} events={report.event_count}")
    print(f"Speakers: {', '.join(f'{label} -> {name}' for label, name in report.speaker_mappings.items())}")
    print(f"Memory answer: {report.memory_answer}")
    print(
        "External auto-agent: "
        f"assignment={report.external_assignment_id} action={report.external_action_id} "
        f"provider={report.external_provider} dispatched={report.external_auto_dispatched}"
    )
    print(f"Approved action: {report.action_id}")
    print(f"Pending visible: Alice={report.pending_seen_by_alice} Bob={report.pending_seen_by_bob}")
    print(f"Completed visible: Alice={report.completed_seen_by_alice} Bob={report.completed_seen_by_bob}")
    print(f"Branch: {report.branch}")
    print(f"Files changed: {', '.join(report.files_changed)}")
    print(f"Tests passed: {report.tests_passed}")
    print(f"Git dirty after approval: {report.git_dirty_after}")
    print(f"Trace events: {report.trace_event_count}")
    print(
        "Trace coverage: "
        + ", ".join(f"{key}={value}" for key, value in sorted(report.trace_coverage.items()))
    )
    if report.rejected_action_id:
        print(f"Rejected action: {report.rejected_action_id}")
    print("Handoff highlights:")
    for line in report.handoff_lines[:6]:
        print(f"  - {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic meeting closure smoke harness.")
    parser.add_argument("--json", action="store_true", help="Print the harness report as JSON.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary workspace for inspection.")
    parser.add_argument("--skip-reject-path", action="store_true", help="Run only the happy-path approval scenario.")
    parser.add_argument(
        "--collab-backend",
        choices=["json", "sqlite"],
        default="json",
        help="Collaboration store backend used by the isolated harness.",
    )
    args = parser.parse_args(argv)

    try:
        report = run_meeting_closure_harness(
            keep_workspace=args.keep_workspace,
            include_reject_path=not args.skip_reject_path,
            collab_backend=args.collab_backend,
        )
    except HarnessFailure as exc:
        print(f"Meeting closure harness failed: {exc}")
        return 1

    print_harness_report(report, as_json=args.json)
    return 0


@contextmanager
def _isolated_client(temp_dir: Path, workspace: Path, *, collab_backend: str) -> Iterator[TestClient]:
    users_path = temp_dir / "users.json"
    store = UserStore(users_path)
    collab_path = temp_dir / "collab.json"
    collab_sqlite_path = temp_dir / "collab.sqlite3"
    if collab_backend == "sqlite":
        collab_store = SQLiteCollaborationStore(collab_sqlite_path)
    else:
        collab_store = CollaborationStore(collab_path)
    collab = CollaborationService(collab_store)
    speakers = SpeakerService(
        SpeakerStore(temp_dir / "speakers.json"),
        collab,
        MockSpeakerProvider(),
    )
    events = RoomEventHub()
    settings = Settings(
        users_store_path=users_path,
        jwt_secret="meeting-closure-test-secret",
        memory_store_path=str(temp_dir / "memory.json"),
        collab_store_backend=collab_backend,
        collab_store_path=collab_path,
        collab_sqlite_path=collab_sqlite_path,
        speaker_store_path=temp_dir / "speakers-unused.json",
        external_agent_store_path=temp_dir / "external-agents.json",
        external_agent_credential_secret="meeting-closure-external-secret",
        agent_runs_path=temp_dir / "agent-runs.json",
        voiceops_cache_path=temp_dir / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        speaker_provider="mock",
        live_chunk_seconds=2.0,
        live_window_seconds=4.0,
        voiceops_workspace=str(workspace),
    )
    external = ExternalAgentService(ExternalAgentCredentialStore(settings.external_agent_store_path), settings, collab)
    agents = MultiAgentService(AgentRunStore(settings.agent_runs_path), collab, settings, external_runner=external)

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_multi_agent_service] = lambda: agents
    app.dependency_overrides[get_speaker_service] = lambda: speakers
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
                return {"service": "meeting-closure"}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (path / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        textwrap.dedent(
            """
            from fastapi.testclient import TestClient
            from app import app

            client = TestClient(app)

            def test_root():
                assert client.get("/").status_code == 200

            def test_health():
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "VoiceOps Tests"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


def _add_origin(path: Path, remote_url: str) -> None:
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=path, check=True, capture_output=True, text=True)


def _git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _token(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    _assert_equal(response.status_code, 200, f"login should work for {email}")
    return response.json()["access_token"]


def _join_room(client: TestClient, token: str) -> None:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/join",
        headers=_auth(token),
        json={"room_name": "Team room", "project": "workspace"},
    )
    _assert_equal(response.status_code, 200, "participant should join the room")


def _bind_room_workspace(client: TestClient, token: str, workspace: Path) -> None:
    response = client.put(
        f"/collab/rooms/{ROOM_ID}/workspace",
        headers=_auth(token),
        json={"path": str(workspace)},
    )
    _assert_equal(response.status_code, 200, "admin should bind the room workspace")


def _map_speakers(client: TestClient, token: str) -> dict[str, str]:
    mappings = {}
    for label, user_id in (("SPEAKER_00", "user-priya"), ("SPEAKER_01", "user-admin")):
        response = client.post(
            f"/speakers/rooms/{ROOM_ID}/mappings",
            headers=_auth(token),
            json={
                "speaker_label": label,
                "user_id": user_id,
                "confidence": 0.93,
                "source": "manual",
            },
        )
        _assert_equal(response.status_code, 200, f"{label} should map to {user_id}")
        data = response.json()
        mappings[label] = data["user_name"]
    return mappings


def _ingest_meeting_segments(client: TestClient, token: str) -> None:
    response = client.post(
        f"/speakers/rooms/{ROOM_ID}/segments",
        headers=_auth(token),
        json={
            "session_id": "meeting-closure-segments",
            "source": "meeting_audio",
            "segments": [
                {
                    "speaker_label": "SPEAKER_00",
                    "start_ms": 0,
                    "end_ms": 1800,
                    "confidence": 0.88,
                    "text": "We decided to keep the health check fix local and need to fix the failing health check in app.py",
                },
                {
                    "speaker_label": "SPEAKER_01",
                    "start_ms": 1900,
                    "end_ms": 3100,
                    "confidence": 0.86,
                    "text": "What is still open before the handoff?",
                },
            ],
        },
    )
    _assert_equal(response.status_code, 200, "meeting speaker segments should ingest")
    _assert_equal(response.json()["message_count"], 2, "two meeting messages should be recorded")


def _query_memory(client: TestClient, token: str) -> str:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/memory/query",
        headers=_auth(token),
        json={"question": "what is still open?", "limit": 8},
    )
    _assert_equal(response.status_code, 200, "memory question should succeed")
    data = response.json()
    _assert_equal(data["mode"], "deterministic", "memory query should stay deterministic")
    _assert_truthy(data["items"], "memory query should return matched items")
    return data["answer"]


def _connect_local_external_agent(client: TestClient, token: str) -> None:
    response = client.post(
        "/external-agents/providers/local/credentials/local-cli",
        headers=_auth(token),
        json={"account_label": "Local Open Agent"},
    )
    _assert_equal(response.status_code, 200, "local external agent should connect for harness")


def _request_external_auto_patch_from_meeting_context(client: TestClient, token: str) -> tuple[dict, dict]:
    response = client.post(
        "/voice/process-text",
        headers=_auth(token),
        json={
            "text": "AI have the best agent fix that",
            "session_id": "meeting-closure-external-fix-that",
            "room_id": ROOM_ID,
            "include_tts": False,
        },
    )
    _assert_equal(response.status_code, 200, "external best-agent request should reach the voice pipeline")
    data = response.json()
    _assert_equal(data["orchestrator_result"], None, "external auto route should suppress internal orchestrator action")

    assignments = client.get(f"/agents/rooms/{ROOM_ID}/assignments", headers=_auth(token))
    _assert_equal(assignments.status_code, 200, "assignment queue should be available")
    assignment = next(
        (
            item for item in assignments.json()["assignments"]
            if item["source"] == "voice_auto_external_assignment"
        ),
        None,
    )
    _assert_truthy(assignment, "voice best-agent request should create an external assignment")

    room = _room_snapshot(client, token)
    _assert_truthy(room["actions"], "external best-agent request should create a pending action")
    return assignment, room["actions"][-1]


def _request_patch_from_meeting_context(client: TestClient, token: str) -> dict:
    response = client.post(
        "/voice/process-text",
        headers=_auth(token),
        json={
            "text": "fix that",
            "session_id": "meeting-closure-fix-that",
            "room_id": ROOM_ID,
            "include_tts": False,
        },
    )
    _assert_equal(response.status_code, 200, "fix-that request should reach the voice pipeline")
    room = _room_snapshot(client, token)
    _assert_truthy(room["actions"], "fix-that request should create an action")
    return room["actions"][-1]


def _request_health_patch(client: TestClient, token: str) -> dict:
    response = client.post(
        "/voice/process-text",
        headers=_auth(token),
        json={
            "text": "fix the failing health check",
            "session_id": "room-pr-health-fix",
            "room_id": ROOM_ID,
            "include_tts": False,
        },
    )
    _assert_equal(response.status_code, 200, "health patch request should reach the voice pipeline")
    room = _room_snapshot(client, token)
    _assert_truthy(room["actions"], "health patch request should create an action")
    return room["actions"][-1]


def _approve_patch(client: TestClient, token: str, action_id: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/actions/{action_id}/approve",
        headers=_auth(token),
        json={"note": "approved by meeting closure harness"},
    )
    _assert_equal(response.status_code, 200, "Bob should approve the pending patch")
    return response.json()


def _commit_patch(client: TestClient, token: str, action_id: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/actions/{action_id}/commit",
        headers=_auth(token),
        json={"message": "VoiceOps: room PR harness"},
    )
    _assert_equal(response.status_code, 200, "Bob should commit the approved room patch")
    return response.json()


def _plan_pull_request(client: TestClient, token: str, action_id: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/actions/{action_id}/pull-request",
        headers=_auth(token),
        json={"dry_run": True, "base_branch": "main", "title": "Fix room health"},
    )
    _assert_equal(response.status_code, 200, "Bob should build a room-scoped PR plan")
    return response.json()


def _reject_patch(
    client: TestClient,
    bob_token: str,
    action_id: str,
    workspace: Path,
    original_app: str,
) -> tuple[str, list[str]]:
    rejected = client.post(
        f"/collab/rooms/{ROOM_ID}/actions/{action_id}/reject",
        headers=_auth(bob_token),
        json={"note": "reject path smoke"},
    )
    _assert_equal(rejected.status_code, 200, "Bob should reject the pending patch")
    _assert_equal(rejected.json()["status"], "rejected", "rejected action should be marked rejected")
    _assert_equal(
        (workspace / "app.py").read_text(encoding="utf-8"),
        original_app,
        "rejecting a patch must leave the workspace unchanged",
    )
    handoff = _get_handoff(client, bob_token)
    _assert_handoff_contains(handoff, "Rejected by Sam Ortiz")
    return action_id, handoff["lines"]


def _room_snapshot(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}", headers=_auth(token))
    _assert_equal(response.status_code, 200, "room snapshot should be available")
    return response.json()


def _action_has_status(client: TestClient, token: str, action_id: str, status: str) -> bool:
    room = _room_snapshot(client, token)
    return any(action["id"] == action_id and action["status"] == status for action in room["actions"])


def _get_git_status(client: TestClient, token: str) -> dict:
    response = client.get("/workspace/git/status", headers=_auth(token))
    _assert_equal(response.status_code, 200, "workspace git status should be available")
    return response.json()


def _get_handoff(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}/handoff", headers=_auth(token))
    _assert_equal(response.status_code, 200, "handoff should be available")
    return response.json()


def _get_event_store_readiness(client: TestClient, token: str) -> dict:
    response = client.get("/system/event-store/readiness", headers=_auth(token))
    _assert_equal(response.status_code, 200, "event-store readiness should be available")
    return response.json()


def _get_room_trace(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}/trace", headers=_auth(token))
    _assert_equal(response.status_code, 200, "room trace should be available")
    return response.json()


def _assert_trace_covers_closure(trace: dict) -> None:
    coverage = trace.get("coverage") or {}
    required = [
        "speaker_segments",
        "memory_items",
        "patch_proposal",
        "approval_decision",
        "git_branch",
        "handoff_summary",
    ]
    missing = [key for key in required if not coverage.get(key)]
    if missing:
        raise HarnessFailure(f"room trace is missing closure coverage {missing!r}; got {coverage!r}")
    events = trace.get("events") or []
    if len(events) < len(required):
        raise HarnessFailure(f"room trace should include enough events to audit closure; got {len(events)}")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_handoff_contains(handoff: dict, expected: str) -> None:
    if not any(expected in line for line in handoff["lines"]):
        raise HarnessFailure(f"handoff should contain {expected!r}; got {handoff['lines']!r}")


def _assert_handoff_not_contains(handoff: dict, unexpected: str) -> None:
    if any(unexpected in line for line in handoff["lines"]):
        raise HarnessFailure(f"handoff should not contain {unexpected!r}; got {handoff['lines']!r}")


def _assert_contains(actual: str, expected: str, message: str) -> None:
    if expected not in actual:
        raise HarnessFailure(f"{message}: expected {expected!r} in {actual!r}")


def _assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise HarnessFailure(f"{message}: expected {expected!r}, got {actual!r}")


def _assert_truthy(actual, message: str) -> None:
    if not actual:
        raise HarnessFailure(f"{message}: got {actual!r}")


if __name__ == "__main__":
    raise SystemExit(main())
