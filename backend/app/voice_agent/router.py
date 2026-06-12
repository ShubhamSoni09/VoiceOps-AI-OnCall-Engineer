from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.voice_agent.models import VoiceProcessRequest, VoiceProcessResponse
from app.voice_agent.pipeline import VoiceAgentPipeline

router = APIRouter(prefix="/voice", tags=["voice-agent"])

_pipeline: VoiceAgentPipeline | None = None


def get_pipeline() -> VoiceAgentPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = VoiceAgentPipeline()
    return _pipeline


@router.post("/process-audio", response_model=VoiceProcessResponse)
async def process_audio(
    audio: UploadFile = File(..., description="Audio file (wav, mp3, webm, etc.)"),
    session_id: str = Form(default="default"),
    incident_context: str = Form(default="{}"),
    include_tts: str = Form(default="false"),
    user: UserPublic = Depends(require_permission("voice:use")),
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
        use_tts = include_tts.strip().lower() in {"true", "1", "yes"}
        return await get_pipeline().process_audio(
            audio_bytes,
            session_id=session_id,
            filename=audio.filename or "audio.wav",
            incident_context=ctx,
            include_tts=use_tts,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Voice processing failed: {exc}") from exc


@router.post("/process-text", response_model=VoiceProcessResponse)
async def process_text(
    body: VoiceProcessRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
) -> VoiceProcessResponse:
    """Process pre-transcribed text (useful for testing or text fallback)."""
    try:
        return await get_pipeline().process_text(
            body.text,
            session_id=body.session_id,
            incident_context=body.incident_context,
            include_tts=body.include_tts,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/process-text-stream")
async def process_text_stream(
    body: VoiceProcessRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
) -> StreamingResponse:
    """SSE endpoint: streams step/output/diff events then a final 'done' event."""

    async def _gen():
        async for chunk in get_pipeline().process_text_stream(
            body.text,
            session_id=body.session_id,
            incident_context=body.incident_context,
            user_id=user.id,
        ):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/sessions/{session_id}")
async def clear_session(
    session_id: str,
    _user: UserPublic = Depends(get_current_user),
) -> dict:
    get_pipeline().clear_session(session_id)
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
        "openai_model": settings.openai_model if settings.llm_provider == "openai" else None,
        "llm_ready": (
            settings.llm_provider == "mock"
            or (settings.llm_provider == "openai" and bool(settings.openai_api_key))
            or settings.llm_provider == "bedrock"
        ),
        "tts_provider": settings.tts_provider,
        "openai_tts_voice": settings.openai_tts_voice if settings.tts_provider == "openai" else None,
        "tts_ready": settings.tts_provider == "mock" or bool(settings.openai_api_key),
        "workspace_connected": bool(resolve_configured_workspace(settings.voiceops_workspace)),
        "workspace": settings.voiceops_workspace,
    }
