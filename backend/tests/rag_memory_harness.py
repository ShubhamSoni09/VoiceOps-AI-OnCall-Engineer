from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import textwrap
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.models import UserPublic
from app.auth.users import UserStore
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.voice_agent.models import OrchestratorResult
import app.voice_agent.router as voice_router


ROOM_ID = "main"
ALICE_EMAIL = "priya@voiceops.dev"
ALICE_PASSWORD = "oncall123"


class RagSmokeFailure(AssertionError):
    """Raised when the deterministic RAG smoke flow breaks."""


@dataclass
class RagSmokeReport:
    room_id: str
    workspace_path: str
    provider: str
    indexed_documents: int
    indexed_sources: dict[str, int]
    file_answer: str
    code_answer: str
    decision_answer: str
    action_answer: str
    action_diff_found: bool
    provenance_answer: str
    provenance_sources: list[str]
    provenance_ontology_hits: int
    provenance_trace: dict
    citation_sources: list[str]
    trace: dict
    action_id: str
    voice_answer: str
    voice_citation_count: int
    voice_trace: dict
    memory_health_status: str
    memory_health_warnings: list[str]
    memory_source_coverage: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


def run_rag_memory_smoke(
    *,
    base_dir: Path | None = None,
    keep_workspace: bool = False,
) -> RagSmokeReport:
    if base_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="voiceops-rag-memory-"))
        cleanup = not keep_workspace
    else:
        temp_dir = base_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False

    try:
        workspace = temp_dir / "workspace"
        _write_workspace(workspace)

        with _isolated_client(temp_dir, workspace) as context:
            client = context.client
            token = _token(client, ALICE_EMAIL, ALICE_PASSWORD)
            _join_room(client, token)
            _seed_meeting_memory(client, token)
            action = _seed_action(context.collab)

            rebuild = client.post(
                f"/collab/rooms/{ROOM_ID}/rag/index?include_code=true",
                headers=_auth(token),
            )
            _assert_equal(rebuild.status_code, 200, "RAG index rebuild should succeed")
            index = rebuild.json()
            _assert_equal(index["provider"], "local_sparse", "RAG smoke should use local sparse provider")
            _assert_truthy(index["document_count"] >= 5, "RAG index should include meeting, action, and code docs")

            file_query = _rag_query(client, token, "what files did Alice mention?")
            code_query = _rag_query(client, token, "what does app.py health return status ok?")
            action_query = _rag_query(client, token, "what patch approval touched app.py?")
            provenance_query = _provenance_query(client, token, "what files did Alice mention and who approved app.py?")
            memory_health = _memory_health(client, token)
            voice = _voice_question(client, token, "what did we decide?")
            room = _room_snapshot(client, token)
            agent_message = room["messages"][-1]
            agent_metadata = agent_message["metadata"]

            _assert_contains(file_query["answer"], "app.py", "file RAG answer should mention app.py")
            _assert_truthy(
                any(item["source"] == "memory" for item in file_query["citations"]),
                "file RAG answer should cite memory",
            )
            _assert_truthy(
                any(item["source"] == "code" for item in code_query["citations"]),
                "code RAG answer should cite workspace code",
            )
            _assert_truthy(
                any(item["source_id"] == action.id for item in action_query["citations"]),
                "action RAG answer should cite the seeded agent action",
            )
            action_diff_found = any(
                item["source_id"] == action.id
                and '@app.get("/health")' in item["metadata"].get("approval", {}).get("diff", "")
                for item in action_query["citations"]
            )
            _assert_truthy(action_diff_found, "action RAG citation should preserve diff metadata")
            _assert_contains(provenance_query["answer"], "app.py", "provenance answer should mention app.py")
            _assert_not_contains(
                provenance_query["answer"],
                "a/app.py",
                "provenance answer should normalize unified diff prefixes",
            )
            _assert_truthy(
                any(item["source"] == "ontology" for item in provenance_query["citations"]),
                "provenance query should cite ontology",
            )
            _assert_truthy(
                provenance_query["retrieval"]["ontology_hits"] > 0,
                "provenance trace should report ontology hits",
            )
            _assert_equal(agent_metadata["source"], "rag_query", "voice memory answer should use RAG")
            _assert_truthy(agent_metadata["citations"], "voice memory answer should persist citations")
            _assert_equal(
                agent_metadata["retrieval"]["provider"],
                "local_sparse",
                "voice memory answer should persist retrieval trace",
            )
            _assert_equal(memory_health["status"], "needs_review", "memory health should expose reviewable open items")
            _assert_truthy(
                memory_health["source_coverage"]["message"] > 0,
                "memory health should report message-backed memory",
            )
            _assert_truthy(
                memory_health["source_coverage"]["action"] > 0,
                "memory health should report action-backed memory",
            )

            return RagSmokeReport(
                room_id=ROOM_ID,
                workspace_path=str(workspace),
                provider=index["provider"],
                indexed_documents=index["document_count"],
                indexed_sources=index["sources"],
                file_answer=file_query["answer"],
                code_answer=code_query["answer"],
                decision_answer=voice["response_text"],
                action_answer=action_query["answer"],
                action_diff_found=action_diff_found,
                provenance_answer=provenance_query["answer"],
                provenance_sources=sorted({item["source"] for item in provenance_query["citations"]}),
                provenance_ontology_hits=provenance_query["retrieval"]["ontology_hits"],
                provenance_trace=provenance_query["retrieval"],
                citation_sources=sorted({item["source"] for item in file_query["citations"]}),
                trace=file_query["retrieval"],
                action_id=action.id,
                voice_answer=voice["response_text"],
                voice_citation_count=len(agent_metadata["citations"]),
                voice_trace=agent_metadata["retrieval"],
                memory_health_status=memory_health["status"],
                memory_health_warnings=memory_health["warnings"],
                memory_source_coverage=memory_health["source_coverage"],
            )
    except AssertionError:
        raise
    except Exception as exc:  # pragma: no cover - keeps manual smoke output readable.
        raise RagSmokeFailure(f"RAG memory smoke setup failed: {exc}") from exc
    finally:
        if cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def print_rag_smoke_report(report: RagSmokeReport, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return

    print("RAG memory smoke passed")
    print(f"Room: {report.room_id}")
    print(f"Workspace: {report.workspace_path}")
    print(f"Provider: {report.provider}")
    print(f"Indexed documents: {report.indexed_documents}")
    print(f"Indexed sources: {report.indexed_sources}")
    print(f"File answer: {report.file_answer}")
    print(f"Code answer: {report.code_answer}")
    print(f"Decision answer: {report.decision_answer}")
    print(f"Action answer: {report.action_answer}")
    print(f"Action diff found: {report.action_diff_found}")
    print(f"Provenance answer: {report.provenance_answer}")
    print(f"Provenance sources: {', '.join(report.provenance_sources)}")
    print(f"Provenance ontology hits: {report.provenance_ontology_hits}")
    print(f"Citation sources: {', '.join(report.citation_sources)}")
    print(
        "Trace: "
        f"short={report.trace.get('short_memory_hits')} "
        f"long={report.trace.get('long_memory_hits')} "
        f"candidates={report.trace.get('candidate_count')}"
    )
    print(f"Voice citations: {report.voice_citation_count}")
    print(f"Memory health: {report.memory_health_status}")
    print(f"Memory source coverage: {report.memory_source_coverage}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic RAG memory smoke harness.")
    parser.add_argument("--json", action="store_true", help="Print the smoke report as JSON.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary workspace for inspection.")
    args = parser.parse_args(argv)

    try:
        report = run_rag_memory_smoke(keep_workspace=args.keep_workspace)
    except RagSmokeFailure as exc:
        print(f"RAG memory smoke failed: {exc}")
        return 1

    print_rag_smoke_report(report, as_json=args.json)
    return 0


@dataclass
class _ClientContext:
    client: TestClient
    collab: CollaborationService


@contextmanager
def _isolated_client(temp_dir: Path, workspace: Path) -> Iterator[_ClientContext]:
    users_path = temp_dir / "users.json"
    store = UserStore(users_path)
    collab = CollaborationService(CollaborationStore(temp_dir / "collab.json"))
    events = RoomEventHub()
    settings = Settings(
        users_store_path=users_path,
        jwt_secret="rag-memory-smoke-secret",
        memory_store_path=str(temp_dir / "memory.json"),
        collab_store_path=temp_dir / "collab-unused.json",
        rag_index_path=temp_dir / "rag-index.json",
        voiceops_cache_path=temp_dir / "cache.json",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        voiceops_workspace=str(workspace),
    )

    old_overrides = dict(app.dependency_overrides)
    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_room_event_hub] = lambda: events

    try:
        with TestClient(app) as client:
            yield _ClientContext(client=client, collab=collab)
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

            @app.get("/health")
            def health():
                return {"status": "ok"}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text("RAG smoke workspace for app.py health checks.\n", encoding="utf-8")


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


def _seed_meeting_memory(client: TestClient, token: str) -> None:
    messages = [
        "We decided app.py should keep the health endpoint explicit.",
        "Alice mentioned app.py and README.md during the review.",
        "Need to keep the rollback risk open until Sam approves the patch.",
    ]
    for text in messages:
        response = client.post(
            f"/collab/rooms/{ROOM_ID}/messages",
            headers=_auth(token),
            json={"text": text, "source": "meeting_audio"},
        )
        _assert_equal(response.status_code, 200, f"meeting message should record: {text}")


def _seed_action(collab: CollaborationService):
    user = UserPublic(
        id="user-priya",
        email=ALICE_EMAIL,
        name="Priya Nair",
        initials="PN",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
    return collab.add_action(
        ROOM_ID,
        user,
        OrchestratorResult(
            executed=False,
            action="patch",
            summary="Pending patch proposal keeps app.py health endpoint explicit.",
            files_changed=["app.py"],
            pending_approval=True,
            approval={
                "status": "pending_approval",
                "diff": "--- a/app.py\n+++ b/app.py\n@@\n @app.get(\"/health\")\n",
                "test_command": "python -m pytest -q",
            },
        ),
    )


def _rag_query(client: TestClient, token: str, question: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/rag/query",
        headers=_auth(token),
        json={"question": question, "limit": 8, "include_code": True},
    )
    _assert_equal(response.status_code, 200, f"RAG query should succeed for {question!r}")
    data = response.json()
    _assert_equal(data["mode"], "local_hybrid", "RAG query should stay local hybrid")
    _assert_truthy(data["citations"], "RAG query should return citations")
    _assert_equal(data["retrieval"]["provider"], "local_sparse", "RAG query should report provider")
    return data


def _provenance_query(client: TestClient, token: str, question: str) -> dict:
    response = client.post(
        f"/collab/rooms/{ROOM_ID}/provenance/query",
        headers=_auth(token),
        json={"question": question, "limit": 12, "include_code": True, "include_ontology": True},
    )
    _assert_equal(response.status_code, 200, f"Provenance query should succeed for {question!r}")
    data = response.json()
    _assert_equal(data["mode"], "local_provenance", "Provenance query should report local provenance mode")
    _assert_equal(data["retrieval"]["provider"], "local_provenance", "Provenance query should report provider")
    _assert_equal(data["retrieval"]["rag_provider"], "local_sparse", "Provenance query should preserve RAG provider")
    _assert_truthy(data["citations"], "Provenance query should return citations")
    return data


def _memory_health(client: TestClient, token: str) -> dict:
    response = client.get(
        f"/collab/rooms/{ROOM_ID}/memory/health",
        headers=_auth(token),
    )
    _assert_equal(response.status_code, 200, "Memory health should be available")
    return response.json()


def _voice_question(client: TestClient, token: str, question: str) -> dict:
    response = client.post(
        "/voice/process-text",
        headers=_auth(token),
        json={
            "text": question,
            "session_id": "rag-memory-smoke",
            "room_id": ROOM_ID,
            "include_tts": False,
        },
    )
    _assert_equal(response.status_code, 200, "voice text memory question should succeed")
    return response.json()


def _room_snapshot(client: TestClient, token: str) -> dict:
    response = client.get(f"/collab/rooms/{ROOM_ID}", headers=_auth(token))
    _assert_equal(response.status_code, 200, "room snapshot should be available")
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_contains(actual: str, expected: str, message: str) -> None:
    if expected not in actual:
        raise RagSmokeFailure(f"{message}: expected {expected!r} in {actual!r}")


def _assert_not_contains(actual: str, unexpected: str, message: str) -> None:
    if unexpected in actual:
        raise RagSmokeFailure(f"{message}: did not expect {unexpected!r} in {actual!r}")


def _assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise RagSmokeFailure(f"{message}: expected {expected!r}, got {actual!r}")


def _assert_truthy(actual, message: str) -> None:
    if not actual:
        raise RagSmokeFailure(f"{message}: got {actual!r}")


if __name__ == "__main__":
    raise SystemExit(main())
