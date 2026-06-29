import textwrap

from app.auth.models import UserPublic
from app.collab.models import TextMessageRequest
from app.collab.service import CollaborationService
from app.collab.store import CollaborationStore
from app.config import Settings
from app.voice_agent.meeting_router import MeetingCommandRouter, looks_like_room_memory_question
from app.voice_agent.models import (
    EnrichedContext,
    ExtractedIntent,
    IncidentAction,
    NormalizedCommand,
    OrchestratorResult,
    SpeechResult,
    VoiceIntent,
    VoiceProcessResponse,
)


def _user() -> UserPublic:
    return UserPublic(
        id="user-sam",
        email="sam@example.com",
        name="Sam Ortiz",
        initials="SO",
        role="admin",
        role_label="Admin",
        permissions=["voice:use"],
    )


def _settings(tmp_path, workspace=None) -> Settings:
    return Settings(
        users_store_path=tmp_path / "users.json",
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        collab_store_path=tmp_path / "collab-unused.json",
        rag_index_path=tmp_path / "rag-index.json",
        voiceops_workspace=str(workspace) if workspace else None,
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
    )


def _response(text: str, action: IncidentAction = IncidentAction.UNKNOWN) -> VoiceProcessResponse:
    intent = ExtractedIntent(intent=VoiceIntent.UNKNOWN, action=action, raw_summary=text)
    command = NormalizedCommand(
        intent=VoiceIntent.UNKNOWN,
        action=action,
        requires_approval=False,
        original_transcript=text,
        normalized_text=text,
    )
    return VoiceProcessResponse(
        session_id="router-test",
        transcript=text,
        intent=intent,
        command=command,
        context=EnrichedContext(transcript=text, intent=intent, command=command),
        response_text="Default pipeline response.",
        speech=SpeechResult(text="Default pipeline response.", provider="browser"),
        orchestrator_result=None,
    )


def test_meeting_router_prioritizes_audit_over_broad_memory_questions(tmp_path):
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    action = collab.add_action(
        "main",
        _user(),
        OrchestratorResult(
            executed=True,
            action="patch",
            summary="Patch proposal for app.py.",
            files_changed=["app.py"],
            approval={
                "status": "approved",
                "decided_by_name": "Sam Ortiz",
                "git": {"branch_name": "voiceops/act-test-fix-health"},
            },
        ),
    )
    router = MeetingCommandRouter(collab=collab, settings=_settings(tmp_path))

    answer = router.answer_read_only_prompt("main", "who approved the patch?", memory_question_mode="broad")

    assert answer is not None
    assert answer.metadata["source"] == "audit_query"
    assert action.id in answer.metadata["matched_items"][0]
    assert "Sam Ortiz approved patch proposal" in answer.text
    assert "voiceops/act-test-fix-health" in answer.text


def test_meeting_router_trace_explains_audit_memory_code_and_patch_routes(tmp_path):
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    collab.add_user_message(
        "main",
        _user(),
        TextMessageRequest(
            text="We decided to keep app.py health checks explicit",
            source="meeting_audio",
        ),
    )
    action = collab.add_action(
        "main",
        _user(),
        OrchestratorResult(
            executed=True,
            action="patch",
            summary="Patch proposal for app.py.",
            files_changed=["app.py"],
            approval={
                "status": "approved",
                "decided_by_name": "Sam Ortiz",
                "git": {"branch_name": "voiceops/act-test-fix-health"},
            },
        ),
    )
    router = MeetingCommandRouter(collab=collab, settings=_settings(tmp_path, workspace=tmp_path))

    audit = router.trace_prompt("main", "who approved the patch?")
    memory = router.trace_prompt("main", "what did we decide?")
    code = router.trace_prompt("main", "explain app.py health endpoint", memory_question_mode="narrow")
    patch = router.trace_prompt("main", "fix that failing health check", memory_question_mode="narrow")

    assert audit.route == "audit_query"
    assert audit.read_only is True
    assert audit.action_policy == "no_workspace_change"
    assert action.id in audit.matched_item_ids[0]
    assert memory.route == "rag_query"
    assert memory.matched_item_ids
    assert memory.metadata["retrieval"]["provider"] == "local_sparse"
    assert memory.metadata["retrieval"]["short_memory_hits"] > 0
    assert code.route == "code_query"
    assert code.read_only is True
    assert patch.route == "agent_pipeline"
    assert patch.read_only is False
    assert patch.action_policy == "approval_required_before_workspace_write"


def test_room_memory_question_supports_chinese_without_capturing_general_questions():
    assert looks_like_room_memory_question("刚刚决定了什么？")
    assert looks_like_room_memory_question("还有哪些任务？")
    assert looks_like_room_memory_question("Alice 提到哪些文件？")
    assert looks_like_room_memory_question("还有哪些问题没解决？")
    assert looks_like_room_memory_question("有什么风险？")

    assert not looks_like_room_memory_question("现在状态怎么样？")
    assert not looks_like_room_memory_question("app.py 是什么文件？")


def test_meeting_router_routes_chinese_room_memory_questions_read_only(tmp_path):
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    collab.add_user_message(
        "main",
        _user(),
        TextMessageRequest(
            text="我们决定修复 app.py 的 health check，还有一个待办是让 Bob review。",
            source="meeting_audio",
        ),
    )
    router = MeetingCommandRouter(collab=collab, settings=_settings(tmp_path, workspace=tmp_path))

    decision = router.trace_prompt("main", "刚刚决定了什么？", memory_question_mode="narrow")
    files = router.trace_prompt("main", "Alice 提到哪些文件？", memory_question_mode="narrow")
    general = router.trace_prompt("main", "现在状态怎么样？", memory_question_mode="narrow")

    assert decision.route == "rag_query"
    assert decision.read_only is True
    assert decision.action_policy == "no_workspace_change"
    assert decision.matched_item_ids
    assert files.route == "rag_query"
    assert files.read_only is True
    assert files.action_policy == "no_workspace_change"
    assert general.route == "agent_pipeline"
    assert general.read_only is False
    assert general.action_policy == "approval_required_if_code_changing"


def test_meeting_router_degrades_memory_route_when_rag_provider_is_invalid(tmp_path):
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    collab.add_user_message(
        "main",
        _user(),
        TextMessageRequest(text="We decided to keep RAG deterministic.", source="meeting_audio"),
    )
    settings = _settings(tmp_path).model_copy(update={"rag_embedding_provider": "paid_cloud_embedding"})
    router = MeetingCommandRouter(collab=collab, settings=settings)
    result = _response("what did we decide?")

    trace = router.trace_prompt("main", result.transcript)
    metadata = router.handle_processed_result(result, "main", memory_question_mode="broad")

    assert trace.route == "rag_query"
    assert trace.metadata["mode"] == "degraded"
    assert "Unsupported RAG embedding provider" in trace.metadata["error"]
    assert metadata["source"] == "rag_query"
    assert metadata["mode"] == "degraded"
    assert "RAG memory is unavailable" in result.response_text
    assert result.orchestrator_result is None


def test_meeting_router_narrow_mode_does_not_capture_general_questions(tmp_path):
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    router = MeetingCommandRouter(collab=collab, settings=_settings(tmp_path))
    result = _response("what is the status?")

    metadata = router.handle_processed_result(result, "main", memory_question_mode="narrow")

    assert metadata == {
        "route_trace": {
            "route": "agent_pipeline",
            "source": "agent_pipeline",
            "reason": "No deterministic read-only meeting route matched; the agent pipeline should interpret it.",
            "read_only": False,
            "action_policy": "approval_required_if_code_changing",
            "matched_item_ids": [],
            "confidence": 0.55,
            "metadata": {},
        }
    }
    assert result.response_text == "Default pipeline response."
    assert result.orchestrator_result is None


def test_meeting_router_stores_route_trace_on_patch_action_result(tmp_path):
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    router = MeetingCommandRouter(collab=collab, settings=_settings(tmp_path))
    result = _response("fix that failing health check", action=IncidentAction.PATCH)
    result.orchestrator_result = OrchestratorResult(
        executed=False,
        action="patch",
        summary="Patch waiting for approval.",
        pending_approval=True,
        approval={"diff": "--- a/app.py\n+++ b/app.py\n"},
    )

    metadata = router.handle_processed_result(result, "main", memory_question_mode="narrow")

    assert metadata["route_trace"]["route"] == "agent_pipeline"
    assert metadata["route_trace"]["action_policy"] == "approval_required_before_workspace_write"
    assert result.orchestrator_result.approval["route_trace"]["route"] == "agent_pipeline"
    assert result.orchestrator_result.approval["route_trace"]["read_only"] is False


def test_meeting_router_enriches_unknown_code_questions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text(
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
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    router = MeetingCommandRouter(collab=collab, settings=_settings(tmp_path, workspace))
    result = _response("what does app.py health endpoint do?")

    metadata = router.handle_processed_result(result, "main", memory_question_mode="narrow")

    assert metadata is not None
    assert metadata["source"] == "code_query"
    assert metadata["references"][0]["path"] == "app.py"
    assert "app.py" in result.response_text
