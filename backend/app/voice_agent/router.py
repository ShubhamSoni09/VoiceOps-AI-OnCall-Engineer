from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.agents.models import AgentAssignmentRequest
from app.agents.service import MultiAgentService, get_multi_agent_service
from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.llm.connections_service import LLMConnectionService, get_llm_connection_service
from app.voice_agent.meeting_router import MeetingCommandRouter
from app.voice_agent.models import VoiceProcessRequest, VoiceProcessResponse
from app.voice_agent.pipeline import VoiceAgentPipeline

router = APIRouter(prefix="/voice", tags=["voice-agent"])

_pipeline: VoiceAgentPipeline | None = None
_pipeline_settings: Settings | None = None
_pipeline_cache: dict[str, VoiceAgentPipeline] = {}

_AUTO_EXTERNAL_SOURCE = "voice_auto_external_assignment"
_AUTO_EXTERNAL_AGENT_ID = "auto"
_AUTO_EXTERNAL_AGENT_LABEL = "Auto external agent"
_AUTO_EXTERNAL_ROUTING_PHRASES = (
    "best agent",
    "best coding agent",
    "right agent",
    "available agent",
    "external agent",
    "choose an agent",
    "pick an agent",
    "codex or claude",
    "claude or codex",
    "cursor or codex",
    "最合适",
    "最好的 agent",
    "外部 agent",
    "自动选择",
)
_AUTO_EXTERNAL_ACTION_MARKERS = (
    "fix",
    "patch",
    "implement",
    "change",
    "update",
    "review",
    "find bug",
    "debug",
    "run test",
    "test",
    "explain",
    "修",
    "改",
    "实现",
    "找 bug",
    "测试",
    "解释",
)


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before sending voice commands.")


def _scoped_session_id(room_id: str, user: UserPublic, session_id: str) -> str:
    return f"{room_id}:{user.id}:{session_id}"


def get_pipeline(settings: Settings = Depends(get_settings)) -> VoiceAgentPipeline:
    global _pipeline, _pipeline_settings
    if _pipeline is None:
        _pipeline_cache.clear()
    cache_key = settings.model_dump_json()
    _pipeline = _pipeline_cache.get(cache_key)
    if _pipeline is None:
        _pipeline = VoiceAgentPipeline(settings=settings)
        _pipeline_cache[cache_key] = _pipeline
    _pipeline_settings = settings
    return _pipeline


@router.post("/process-audio", response_model=VoiceProcessResponse)
async def process_audio(
    audio: UploadFile = File(..., description="Audio file (wav, mp3, webm, etc.)"),
    session_id: str = Form(default="default"),
    room_id: str = Form(default="main"),
    incident_context: str = Form(default="{}"),
    include_tts: str = Form(default="false"),
    user: UserPublic = Depends(require_permission("voice:use")),
    collab: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    agents: MultiAgentService = Depends(get_multi_agent_service),
    settings: Settings = Depends(get_settings),
    llm_connections: LLMConnectionService = Depends(get_llm_connection_service),
    pipeline: VoiceAgentPipeline = Depends(get_pipeline),
) -> VoiceProcessResponse:
    """Push-to-talk endpoint: upload audio, get normalized command + enriched context."""
    import json

    try:
        ctx = json.loads(incident_context) if incident_context else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="incident_context must be valid JSON") from exc

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    try:
        _ensure_room_access(room_id, user, collab)
        user_settings = llm_connections.settings_for_user(user, settings)
        room_settings = collab.settings_for_room(room_id, user_settings)
        active_pipeline = pipeline if room_settings == settings else get_pipeline(room_settings)
        use_tts = include_tts.strip().lower() in {"true", "1", "yes"}
        transcription = await active_pipeline.transcribe_audio(
            audio_bytes,
            filename=audio.filename or "audio.wav",
        )
        ctx["room_id"] = room_id
        resolved_context = collab.resolve_recent_task_context(room_id, transcription.text)
        if resolved_context:
            ctx["resolved_context"] = resolved_context
        result = await active_pipeline.process_text(
            transcription.text,
            session_id=_scoped_session_id(room_id, user, session_id),
            incident_context=ctx,
            include_tts=use_tts,
        )
        result = result.model_copy(update={"session_id": session_id})
        agent_metadata = await _maybe_create_auto_external_assignment(
            result,
            room_id,
            user,
            collab,
            agents,
            room_settings,
            resolved_context=resolved_context,
        )
        if agent_metadata:
            await events.publish(
                room_id,
                "agent_assignment_updated",
                actor_id=user.id,
                payload={"assignment_id": agent_metadata["assignment_id"]},
            )
        else:
            agent_metadata = _maybe_handle_room_read_only(result, room_id, collab, room_settings, events)
        collab.record_voice_response(room_id, user, result, source="audio", agent_metadata=agent_metadata)
        await events.publish(room_id, "voice_response_recorded", actor_id=user.id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Voice processing failed: {exc}") from exc


@router.post("/process-text", response_model=VoiceProcessResponse)
async def process_text(
    body: VoiceProcessRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
    collab: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    agents: MultiAgentService = Depends(get_multi_agent_service),
    settings: Settings = Depends(get_settings),
    llm_connections: LLMConnectionService = Depends(get_llm_connection_service),
    pipeline: VoiceAgentPipeline = Depends(get_pipeline),
) -> VoiceProcessResponse:
    """Process pre-transcribed text (useful for testing or text fallback)."""
    try:
        _ensure_room_access(body.room_id, user, collab)
        user_settings = llm_connections.settings_for_user(user, settings)
        room_settings = collab.settings_for_room(body.room_id, user_settings)
        active_pipeline = pipeline if room_settings == settings else get_pipeline(room_settings)
        incident_context = dict(body.incident_context)
        incident_context["room_id"] = body.room_id
        resolved_context = collab.resolve_recent_task_context(body.room_id, body.text)
        if resolved_context:
            incident_context["resolved_context"] = resolved_context
        result = await active_pipeline.process_text(
            body.text,
            session_id=_scoped_session_id(body.room_id, user, body.session_id),
            incident_context=incident_context,
            include_tts=body.include_tts,
        )
        result = result.model_copy(update={"session_id": body.session_id})
        agent_metadata = await _maybe_create_auto_external_assignment(
            result,
            body.room_id,
            user,
            collab,
            agents,
            room_settings,
            resolved_context=resolved_context,
        )
        if agent_metadata:
            await events.publish(
                body.room_id,
                "agent_assignment_updated",
                actor_id=user.id,
                payload={"assignment_id": agent_metadata["assignment_id"]},
            )
        else:
            agent_metadata = _maybe_handle_room_read_only(result, body.room_id, collab, room_settings, events)
        collab.record_voice_response(body.room_id, user, result, source="text", agent_metadata=agent_metadata)
        await events.publish(body.room_id, "voice_response_recorded", actor_id=user.id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/sessions/{session_id}")
async def clear_session(
    session_id: str,
    room_id: str = Query(default="main"),
    user: UserPublic = Depends(require_permission("voice:use")),
    collab: CollaborationService = Depends(get_collaboration_service),
    settings: Settings = Depends(get_settings),
    llm_connections: LLMConnectionService = Depends(get_llm_connection_service),
) -> dict:
    _ensure_room_access(room_id, user, collab)
    user_settings = llm_connections.settings_for_user(user, settings)
    room_settings = collab.settings_for_room(room_id, user_settings)
    pipeline = get_pipeline(room_settings)
    pipeline.clear_session(_scoped_session_id(room_id, user, session_id))
    return {"status": "cleared", "session_id": session_id}


@router.get("/health")
async def voice_health() -> dict:
    from app.config import get_settings
    from app.workspace.tools import resolve_configured_workspace

    settings = get_settings()
    return {
        "status": "ok",
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "llm_ready": (
            settings.llm_provider == "mock"
            or (settings.llm_provider == "openai" and bool(settings.openai_api_key))
            or (settings.llm_provider == "anthropic" and bool(settings.anthropic_api_key))
            or (settings.llm_provider == "openai_compatible" and bool(settings.openai_compatible_base_url))
            or settings.llm_provider == "bedrock"
        ),
        "tts_provider": settings.tts_provider,
        "tts_ready": settings.tts_provider == "mock" or bool(settings.openai_api_key),
        "workspace_connected": bool(resolve_configured_workspace(settings.voiceops_workspace)),
    }


def _maybe_handle_room_read_only(
    result: VoiceProcessResponse,
    room_id: str,
    collab: CollaborationService,
    settings: Settings,
    events: RoomEventHub,
) -> dict | None:
    return MeetingCommandRouter(collab=collab, settings=settings, events=events).handle_processed_result(
        result,
        room_id,
        memory_question_mode="narrow",
    )


async def _maybe_create_auto_external_assignment(
    result: VoiceProcessResponse,
    room_id: str,
    user: UserPublic,
    collab: CollaborationService,
    agents: MultiAgentService,
    settings: Settings,
    *,
    resolved_context: dict | None = None,
) -> dict | None:
    transcript = result.transcript.strip()
    if not _looks_like_auto_external_assignment(transcript):
        return None

    mode = _auto_external_mode(transcript)
    route_trace = MeetingCommandRouter(
        collab=collab,
        settings=settings,
    ).trace_prompt(room_id, transcript, memory_question_mode="narrow").model_dump(mode="json")
    task = _auto_external_task(transcript, resolved_context)
    assignment = agents.create_assignment(
        room_id,
        user,
        AgentAssignmentRequest(
            agent_id=_AUTO_EXTERNAL_AGENT_ID,
            agent_label=_AUTO_EXTERNAL_AGENT_LABEL,
            agent_kind="external",
            task=task,
            mode=mode,
            source=_AUTO_EXTERNAL_SOURCE,
            metadata={
                "source": _AUTO_EXTERNAL_SOURCE,
                "voice_transcript": transcript,
                "resolved_context": resolved_context,
                "route_trace": route_trace,
                "assignment_origin": "voice",
            },
        ),
    )
    dispatched = None
    dispatch_error = None
    if _should_auto_dispatch_external_assignment(transcript, assignment.metadata):
        try:
            dispatched = await agents.dispatch_assignment(room_id, assignment.id, user)
        except ValueError as exc:
            dispatch_error = str(exc)
    final_assignment = dispatched or assignment

    result.response_text = _auto_external_response_text(mode, final_assignment.status.value, final_assignment, dispatch_error)
    result.speech = result.speech.model_copy(update={"text": result.response_text})
    result.orchestrator_result = None
    return {
        "source": _AUTO_EXTERNAL_SOURCE,
        "assignment_id": final_assignment.id,
        "assignment_status": final_assignment.status.value,
        "assignment_action_id": final_assignment.action_id,
        "assignment_run_id": final_assignment.run_id,
        "assignment_mode": mode,
        "agent_id": final_assignment.agent_id,
        "agent_kind": final_assignment.agent_kind,
        "auto_dispatched": dispatched is not None,
        "dispatch_error": dispatch_error,
        "route_trace": route_trace,
        "resolved_context": resolved_context,
    }


def _looks_like_auto_external_assignment(text: str) -> bool:
    lower = text.lower()
    has_auto_route = any(phrase in lower or phrase in text for phrase in _AUTO_EXTERNAL_ROUTING_PHRASES)
    has_agent_name = any(marker in lower for marker in ("codex", "claude", "cursor", "agent"))
    has_action = any(marker in lower or marker in text for marker in _AUTO_EXTERNAL_ACTION_MARKERS)
    return has_action and (has_auto_route or ("agent" in lower and "best" in lower) or has_agent_name and "auto" in lower)


def _auto_external_mode(text: str) -> str:
    lower = text.lower()
    if any(marker in lower or marker in text for marker in ("run test", "tests", "test", "测试")):
        return "test"
    if any(marker in lower or marker in text for marker in ("explain", "解释")):
        return "explain"
    if any(marker in lower or marker in text for marker in ("review", "find bug", "debug", "找 bug")):
        return "review"
    return "patch"


def _auto_external_task(text: str, resolved_context: dict | None) -> str:
    transcript = " ".join(text.strip().split())
    if resolved_context and "that" in transcript.lower():
        context_text = str(resolved_context.get("text") or resolved_context.get("summary") or "").strip()
        if context_text:
            return f"{transcript}\nResolved context: {context_text}"[:1200]
    return transcript[:1200]


def _should_auto_dispatch_external_assignment(text: str, assignment_metadata: dict) -> bool:
    lower = text.lower()
    if assignment_metadata.get("recommendation_ready") is not True:
        return False
    return any(
        marker in lower or marker in text
        for marker in (
            "fix",
            "patch",
            "implement",
            "review",
            "run test",
            "test",
            "explain",
            "修",
            "改",
            "实现",
            "测试",
            "解释",
        )
    )


def _auto_external_response_text(mode: str, status: str, assignment, dispatch_error: str | None) -> str:
    if dispatch_error:
        return f"Queued Auto external agent for {mode}, but dispatch needs attention: {dispatch_error}"
    if assignment.action_id:
        return (
            f"Auto external agent proposed a {mode} change and is waiting for approval. "
            f"Assignment {assignment.id} produced action {assignment.action_id}."
        )
    if status == "completed":
        return f"Auto external agent completed the {mode} assignment."
    if status == "failed":
        return f"Auto external agent could not complete the {mode} assignment: {assignment.note or 'check assignment details'}."
    return (
        f"Queued Auto external agent for {mode}. "
        "It will choose the provider at dispatch time and code changes will wait for approval."
    )
