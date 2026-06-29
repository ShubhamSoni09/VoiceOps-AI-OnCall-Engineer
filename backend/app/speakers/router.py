import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.auth.dependencies import get_user_store
from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import ROLE_PERMISSIONS, UserPublic
from app.auth.security import decode_access_token, to_public_user
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.speakers.live import LiveChunkRejected, LiveMeetingSession, PendingLiveChunk, RollingAudioBuffer
from app.speakers.models import (
    LiveAudioChunkEvent,
    LiveStartEvent,
    SegmentIngestRequest,
    SegmentIngestResponse,
    SpeakerMapping,
    SpeakerMappingRequest,
    SpeakerRoomState,
    SpeakerValidationReport,
)
from app.speakers.service import SpeakerService, get_speaker_service
from app.voice_agent.meeting_router import MeetingCommandRouter
from app.voice_agent.pipeline import VoiceAgentPipeline
from app.voice_agent.router import get_pipeline, _scoped_session_id

router = APIRouter(prefix="/speakers", tags=["speakers"])


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before accessing speaker data.")


@dataclass
class LiveSocketRuntime:
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False


@dataclass
class LiveSessionRegistry:
    sessions: dict[tuple[str, str], LiveMeetingSession] = field(default_factory=dict)

    def start(
        self,
        *,
        room_id: str,
        session_id: str,
        mime_type: str,
        chunk_seconds: float,
        window_seconds: float,
        max_chunk_bytes: int,
        resume_session_id: str | None = None,
        last_sequence: int = 0,
        idle_ttl_seconds: float | None = None,
    ) -> tuple[LiveMeetingSession, bool, bool]:
        self.prune_idle(ttl_seconds=idle_ttl_seconds)
        target_session_id = resume_session_id or session_id
        key = (room_id, target_session_id)
        restored = False
        if resume_session_id and key in self.sessions:
            session = self.sessions[key]
            session.mime_type = mime_type
            session.max_chunk_bytes = max_chunk_bytes
            session.mark_reconnected(last_sequence=last_sequence)
            restored = True
            return session, True, restored

        session = LiveMeetingSession(
            room_id=room_id,
            session_id=target_session_id,
            mime_type=mime_type,
            buffer=RollingAudioBuffer(
                chunk_seconds=chunk_seconds,
                window_seconds=window_seconds,
            ),
            max_chunk_bytes=max_chunk_bytes,
            next_sequence=max(last_sequence + 1, 1),
            latest_accepted_sequence=last_sequence if resume_session_id else 0,
        )
        self.sessions[key] = session
        return session, bool(resume_session_id), restored

    def stop(self, session: LiveMeetingSession) -> None:
        self.sessions.pop((session.room_id, session.session_id), None)

    def prune_idle(self, *, ttl_seconds: float | None, now: float | None = None) -> int:
        if ttl_seconds is None or ttl_seconds <= 0:
            return 0
        current = time.monotonic() if now is None else now
        stale = [
            key
            for key, session in self.sessions.items()
            if current - session.last_seen_at > ttl_seconds
        ]
        for key in stale:
            self.sessions.pop(key, None)
        return len(stale)


_live_session_registry = LiveSessionRegistry()


@router.get("/rooms/{room_id}", response_model=SpeakerRoomState)
async def room_speakers(
    room_id: str,
    user: UserPublic = Depends(get_current_user),
    service: SpeakerService = Depends(get_speaker_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> SpeakerRoomState:
    _ensure_room_access(room_id, user, collab)
    return service.room_state(room_id)


@router.get("/rooms/{room_id}/validation", response_model=SpeakerValidationReport)
async def room_speaker_validation(
    room_id: str,
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    service: SpeakerService = Depends(get_speaker_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> SpeakerValidationReport:
    _ensure_room_access(room_id, user, collab)
    return service.validation_report(room_id, settings)


@router.post("/rooms/{room_id}/segments", response_model=SegmentIngestResponse)
async def ingest_segments(
    room_id: str,
    body: SegmentIngestRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
    service: SpeakerService = Depends(get_speaker_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> SegmentIngestResponse:
    _ensure_room_access(room_id, user, collab)
    response = service.ingest_segments(room_id, body)
    if response.message_count:
        await events.publish(room_id, "speaker_segments_ingested", actor_id=user.id)
    return response


@router.post("/rooms/{room_id}/mappings", response_model=SpeakerMapping)
async def map_speaker(
    room_id: str,
    body: SpeakerMappingRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
    service: SpeakerService = Depends(get_speaker_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> SpeakerMapping:
    _ensure_room_access(room_id, user, collab)
    try:
        mapping = service.map_speaker(room_id, body, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await events.publish(room_id, "speaker_mapping_updated", actor_id=user.id)
    return mapping


@router.websocket("/rooms/{room_id}/live")
async def live_room_audio(
    websocket: WebSocket,
    room_id: str,
) -> None:
    try:
        user = _websocket_user(websocket)
    except Exception:
        await websocket.close(code=1008, reason="Not authenticated")
        return
    if "voice:use" not in ROLE_PERMISSIONS.get(user.role, set()):
        await websocket.close(code=1008, reason="voice:use permission required")
        return

    collab = _websocket_dependency(websocket, get_collaboration_service)
    if not collab.user_can_access_room(room_id, user):
        await websocket.close(code=1008, reason="Join this room before streaming live audio")
        return

    await websocket.accept()
    service = _websocket_dependency(websocket, get_speaker_service)
    events = _websocket_dependency(websocket, get_room_event_hub)
    settings = _websocket_dependency(websocket, get_settings)
    pipeline = get_pipeline(settings)
    live_session: LiveMeetingSession | None = None
    binary_sequence = 1
    runtime = LiveSocketRuntime()

    await _send_status(websocket, runtime, "listening", "Live meeting socket connected")
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                runtime.closed = True
                break
            if message.get("text") is not None:
                payload = _json_payload(message["text"])
                event_type = payload.get("type")
                if event_type == "start":
                    start = LiveStartEvent.model_validate(payload)
                    live_session, resumed, restored = _live_session_registry.start(
                        room_id=room_id,
                        session_id=start.session_id,
                        mime_type=start.mime_type,
                        chunk_seconds=settings.live_chunk_seconds,
                        window_seconds=settings.live_window_seconds,
                        max_chunk_bytes=settings.live_max_chunk_bytes,
                        resume_session_id=start.resume_session_id,
                        last_sequence=start.last_sequence,
                        idle_ttl_seconds=settings.live_session_idle_ttl_seconds,
                    )
                    binary_sequence = live_session.next_sequence
                    await _send_status(
                        websocket,
                        runtime,
                        "listening",
                        "Live meeting reconnected" if resumed else "Live meeting started",
                        resumed=resumed,
                        resume_restored=restored,
                        **_live_runtime_metadata(settings, service.provider_name),
                        **live_session.stats(),
                    )
                elif event_type == "audio_chunk":
                    if live_session is None:
                        await _send_error(websocket, runtime, "not_started", "Send start before audio_chunk")
                        continue
                    chunk = LiveAudioChunkEvent.model_validate(payload)
                    audio_bytes = _decode_audio_payload(chunk.audio_base64)
                    await _process_live_chunk(
                        websocket,
                        service,
                        live_session,
                        audio_bytes,
                        user=user,
                        sequence=chunk.sequence,
                        mime_type=chunk.mime_type or live_session.mime_type,
                        debug_text=chunk.debug_text,
                        collab=collab,
                        events=events,
                        settings=settings,
                        pipeline=pipeline,
                        processing_timeout_seconds=settings.live_processing_timeout_seconds,
                        runtime=runtime,
                    )
                elif event_type == "stop":
                    await _stop_live_session(
                        websocket,
                        service,
                        live_session,
                        user=user,
                        collab=collab,
                        events=events,
                        settings=settings,
                        pipeline=pipeline,
                        processing_timeout_seconds=settings.live_processing_timeout_seconds,
                        runtime=runtime,
                    )
                    runtime.closed = True
                    break
                else:
                    await _send_error(websocket, runtime, "unknown_event", f"Unsupported event: {event_type}")
            elif message.get("bytes") is not None:
                if live_session is None:
                    await _send_error(websocket, runtime, "not_started", "Send start before binary audio")
                    continue
                sequence = binary_sequence
                binary_sequence += 1
                await _process_live_chunk(
                    websocket,
                    service,
                    live_session,
                    message["bytes"],
                    user=user,
                    sequence=sequence,
                    mime_type=live_session.mime_type,
                    collab=collab,
                    events=events,
                    settings=settings,
                    pipeline=pipeline,
                    processing_timeout_seconds=settings.live_processing_timeout_seconds,
                    runtime=runtime,
                )
    except WebSocketDisconnect:
        runtime.closed = True
        return
    except (ValidationError, ValueError) as exc:
        await _send_status(
            websocket,
            runtime,
            "degraded",
            "Live meeting event could not be processed",
            stage="bad_event",
        )
        await _send_error(websocket, runtime, "bad_event", str(exc), recoverable=True)
    finally:
        runtime.closed = True


async def _process_live_chunk(
    websocket: WebSocket,
    service: SpeakerService,
    live_session: LiveMeetingSession,
    audio_bytes: bytes,
    *,
    user: UserPublic,
    sequence: int,
    mime_type: str,
    collab: CollaborationService,
    events: RoomEventHub,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    processing_timeout_seconds: float,
    runtime: LiveSocketRuntime,
    debug_text: str | None = None,
) -> None:
    try:
        resolved_sequence, _window, chunk_stats = live_session.add_chunk(
            audio_bytes,
            sequence=sequence,
            mime_type=mime_type,
        )
    except LiveChunkRejected as exc:
        await _send_status(
            websocket,
            runtime,
            "degraded",
            exc.message,
            stage=exc.reason,
            sequence=exc.sequence,
            elapsed_ms=0,
            chunk_size_bytes=exc.size_bytes,
            **exc.stats,
        )
        return
    await _send_status(
        websocket,
        runtime,
        "processing",
        "Audio uploaded; preparing speaker processing",
        stage="received",
        sequence=resolved_sequence,
        elapsed_ms=0,
        chunk_size_bytes=len(audio_bytes),
        **chunk_stats,
    )
    if live_session.processing_task is not None and not live_session.processing_task.done():
        live_session.skipped_chunks += 1
        live_session.queue_pending_chunk(
            PendingLiveChunk(
                sequence=resolved_sequence,
                data=audio_bytes,
                mime_type=mime_type,
                debug_text=debug_text,
            )
        )
        await _send_status(
            websocket,
            runtime,
            "degraded",
            "Previous audio window is still processing; queued the latest chunk",
            stage="busy",
            sequence=resolved_sequence,
            elapsed_ms=0,
            **live_session.stats(),
        )
        return

    await _start_live_processing(
        websocket,
        service,
        live_session,
        audio_bytes,
        user=user,
        sequence=resolved_sequence,
        mime_type=mime_type,
        debug_text=debug_text,
        collab=collab,
        events=events,
        settings=settings,
        pipeline=pipeline,
        processing_timeout_seconds=processing_timeout_seconds,
        runtime=runtime,
    )


async def _start_live_processing(
    websocket: WebSocket,
    service: SpeakerService,
    live_session: LiveMeetingSession,
    audio_bytes: bytes,
    *,
    user: UserPublic,
    sequence: int,
    mime_type: str,
    collab: CollaborationService,
    events: RoomEventHub,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    processing_timeout_seconds: float,
    runtime: LiveSocketRuntime,
    debug_text: str | None = None,
) -> None:
    started_at = time.perf_counter()
    stage_queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit_stage(stage: str, message: str | None = None) -> None:
        loop.call_soon_threadsafe(
            stage_queue.put_nowait,
            {
                "stage": stage,
                "message": message or _stage_message(stage),
            },
        )

    task = asyncio.create_task(
        service.process_live_chunk(
            live_session.room_id,
            session_id=live_session.session_id,
            sequence=sequence,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            debug_text=debug_text,
            stage_callback=emit_stage,
        )
    )
    live_session.processing_task = task
    timed_out = False
    try:
        while not task.done():
            elapsed = time.perf_counter() - started_at
            if not timed_out and elapsed >= processing_timeout_seconds:
                timed_out = True
                await _send_status(
                    websocket,
                    runtime,
                    "degraded",
                    _slow_processing_message(service.provider_name),
                    stage="processing_slow",
                    timeout_seconds=processing_timeout_seconds,
                    sequence=sequence,
                    elapsed_ms=round(elapsed * 1000),
                    **_live_degraded_metadata(service.provider_name, "processing_slow"),
                    **live_session.stats(),
                )
                asyncio.create_task(
                    _finish_live_chunk(
                        websocket,
                        runtime,
                        task,
                        stage_queue,
                        started_at=started_at,
                        live_session=live_session,
                        sequence=sequence,
                        provider_name=service.provider_name,
                        service=service,
                        user=user,
                        collab=collab,
                        events=events,
                        settings=settings,
                        pipeline=pipeline,
                        processing_timeout_seconds=processing_timeout_seconds,
                        late_correction=True,
                    )
                )
                return
            wait_timeout = 0.1
            wait_timeout = max(0.001, min(wait_timeout, processing_timeout_seconds - elapsed))
            try:
                stage_event = await asyncio.wait_for(stage_queue.get(), timeout=wait_timeout)
            except asyncio.TimeoutError:
                continue
            await _emit_stage_event(
                websocket,
                runtime,
                stage_event,
                live_session=live_session,
                sequence=sequence,
                started_at=started_at,
            )
        result = await task
        while not stage_queue.empty():
            stage_event = stage_queue.get_nowait()
            await _emit_stage_event(
                websocket,
                runtime,
                stage_event,
                live_session=live_session,
                sequence=sequence,
                started_at=started_at,
            )
    except Exception as exc:
        await _send_status(
            websocket,
            runtime,
            "degraded",
            "Live speaker processing unavailable",
            stage="failed",
            sequence=sequence,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            **_live_degraded_metadata(service.provider_name, "failed"),
            **live_session.stats(),
        )
        await _send_error(websocket, runtime, "provider_error", str(exc), recoverable=True)
        if live_session.processing_task is task:
            live_session.processing_task = None
        return

    try:
        await _emit_live_result(
            websocket,
            runtime,
            result,
            live_session=live_session,
            sequence=sequence,
            started_at=started_at,
            user=user,
            collab=collab,
            events=events,
            settings=settings,
            pipeline=pipeline,
            late_correction=False,
        )
    finally:
        if live_session.processing_task is task:
            live_session.processing_task = None
        await _process_queued_live_chunk(
            websocket,
            service,
            live_session,
            user=user,
            collab=collab,
            events=events,
            settings=settings,
            pipeline=pipeline,
            processing_timeout_seconds=processing_timeout_seconds,
            runtime=runtime,
        )


async def _finish_live_chunk(
    websocket: WebSocket,
    runtime: LiveSocketRuntime,
    task: asyncio.Task,
    stage_queue: asyncio.Queue[dict],
    *,
    started_at: float,
    live_session: LiveMeetingSession,
    sequence: int,
    provider_name: str,
    service: SpeakerService,
    user: UserPublic,
    collab: CollaborationService,
    events: RoomEventHub,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    processing_timeout_seconds: float,
    late_correction: bool,
) -> None:
    try:
        result = await task
        while not stage_queue.empty():
            stage_event = stage_queue.get_nowait()
            await _emit_stage_event(
                websocket,
                runtime,
                stage_event,
                live_session=live_session,
                sequence=sequence,
                started_at=started_at,
            )
    except Exception as exc:
        await _send_status(
            websocket,
            runtime,
            "degraded",
            "Live speaker processing unavailable",
            stage="failed",
            sequence=sequence,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            **_live_degraded_metadata(provider_name, "failed"),
            **live_session.stats(),
        )
        await _send_error(websocket, runtime, "provider_error", str(exc), recoverable=True)
        if live_session.processing_task is task:
            live_session.processing_task = None
        return
    try:
        await _emit_live_result(
            websocket,
            runtime,
            result,
            live_session=live_session,
            sequence=sequence,
            started_at=started_at,
            user=user,
            collab=collab,
            events=events,
            settings=settings,
            pipeline=pipeline,
            late_correction=late_correction,
        )
    finally:
        if live_session.processing_task is task:
            live_session.processing_task = None
        await _process_queued_live_chunk(
            websocket,
            service,
            live_session,
            user=user,
            collab=collab,
            events=events,
            settings=settings,
            pipeline=pipeline,
            processing_timeout_seconds=processing_timeout_seconds,
            runtime=runtime,
        )


async def _process_queued_live_chunk(
    websocket: WebSocket,
    service: SpeakerService,
    live_session: LiveMeetingSession,
    *,
    user: UserPublic,
    collab: CollaborationService,
    events: RoomEventHub,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    processing_timeout_seconds: float,
    runtime: LiveSocketRuntime,
) -> None:
    if runtime.closed:
        return
    if live_session.processing_task is not None and not live_session.processing_task.done():
        return
    pending = live_session.pop_pending_chunk()
    if pending is None:
        return
    await _send_status(
        websocket,
        runtime,
        "processing",
        "Processing queued live audio",
        stage="queued_processing",
        sequence=pending.sequence,
        elapsed_ms=0,
        chunk_size_bytes=pending.size_bytes,
        **live_session.stats(),
    )
    await _start_live_processing(
        websocket,
        service,
        live_session,
        pending.data,
        user=user,
        sequence=pending.sequence,
        mime_type=pending.mime_type,
        debug_text=pending.debug_text,
        collab=collab,
        events=events,
        settings=settings,
        pipeline=pipeline,
        processing_timeout_seconds=processing_timeout_seconds,
        runtime=runtime,
    )


async def _stop_live_session(
    websocket: WebSocket,
    service: SpeakerService,
    live_session: LiveMeetingSession | None,
    *,
    user: UserPublic,
    collab: CollaborationService,
    events: RoomEventHub,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    processing_timeout_seconds: float,
    runtime: LiveSocketRuntime,
) -> None:
    if live_session is None:
        await _send_status(websocket, runtime, "stopped", "Live meeting stopped")
        return

    live_session.mark_stopped()
    processing_pending = live_session.processing_task is not None and not live_session.processing_task.done()
    if not processing_pending and live_session.pending_chunk is not None:
        await _process_queued_live_chunk(
            websocket,
            service,
            live_session,
            user=user,
            collab=collab,
            events=events,
            settings=settings,
            pipeline=pipeline,
            processing_timeout_seconds=processing_timeout_seconds,
            runtime=runtime,
        )
        processing_pending = live_session.processing_task is not None and not live_session.processing_task.done()

    await _send_status(
        websocket,
        runtime,
        "stopped",
        "Live meeting stopped",
        stage="stopped",
        processing_pending=processing_pending,
        pending_finalization=processing_pending or live_session.pending_chunk is not None,
        **live_session.stats(),
    )
    _live_session_registry.stop(live_session)


async def _emit_live_result(
    websocket: WebSocket,
    runtime: LiveSocketRuntime,
    result,
    *,
    live_session: LiveMeetingSession,
    sequence: int,
    started_at: float,
    user: UserPublic,
    collab: CollaborationService,
    events: RoomEventHub,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    late_correction: bool = False,
) -> None:
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    live_session.mark_processed(elapsed_ms)
    if result.partial_text and live_session.should_emit_partial(
        temp_id=result.temp_id,
        text=result.partial_text,
        source="provider_result",
    ):
        await _send_json(
            websocket,
            runtime,
            {
                "type": "partial_transcript",
                "temp_id": result.temp_id,
                "text": result.partial_text,
                "start_ms": result.start_ms,
                "end_ms": result.end_ms,
            }
        )
    if result.segments:
        segment_payloads = [segment.model_dump(mode="json") for segment in result.segments]
        if not live_session.should_emit_segments(temp_id=result.temp_id, segment_payloads=segment_payloads):
            await _send_status(
                websocket,
                runtime,
                "listening",
                "Duplicate live speaker result suppressed",
                stage="duplicate_result",
                sequence=sequence,
                elapsed_ms=elapsed_ms,
                **live_session.stats(),
            )
            return
        await _send_json(
            websocket,
            runtime,
            {
                "type": "speaker_segments",
                "temp_id": result.temp_id,
                "segments": segment_payloads,
                "late_correction": late_correction,
            }
        )
        await _send_json(
            websocket,
            runtime,
            {
                "type": "speaker_correction",
                "temp_id": result.temp_id,
                "segment": result.segments[0].model_dump(mode="json"),
                "late_correction": late_correction,
            }
        )
        agent_reply = await _maybe_call_agent(
            room_id=live_session.room_id,
            session_id=live_session.session_id,
            segments=result.segments,
            user=user,
            collab=collab,
            settings=settings,
            pipeline=pipeline,
            wake_words=collab.agent_wake_words,
            events=events,
        )
        if agent_reply:
            await _send_json(websocket, runtime, {"type": "agent_response", **agent_reply})
            await events.publish(live_session.room_id, "agent_response_created", actor_id=user.id)
        await events.publish(live_session.room_id, "speaker_segments_ingested", actor_id=user.id)
    await _send_status(
        websocket,
        runtime,
        "listening",
        "Live meeting is listening",
        stage="completed",
        sequence=sequence,
        elapsed_ms=elapsed_ms,
        correction_pending=False,
        caption_fallback=False,
        late_correction=late_correction,
        **live_session.stats(),
    )


async def _emit_stage_event(
    websocket: WebSocket,
    runtime: LiveSocketRuntime,
    stage_event: dict,
    *,
    live_session: LiveMeetingSession,
    sequence: int,
    started_at: float,
) -> None:
    stage = stage_event["stage"]
    message = stage_event["message"]
    if stage == "transcript_preview" and message:
        temp_id = f"{live_session.session_id}-{sequence}"
        if not live_session.should_emit_partial(temp_id=temp_id, text=message, source="asr_preview"):
            return
        await _send_json(
            websocket,
            runtime,
            {
                "type": "partial_transcript",
                "temp_id": temp_id,
                "text": message,
                "start_ms": None,
                "end_ms": None,
                "provisional": True,
                "source": "asr_preview",
            },
        )
        return
    await _send_status(
        websocket,
        runtime,
        "processing",
        message,
        stage=stage,
        sequence=sequence,
        elapsed_ms=round((time.perf_counter() - started_at) * 1000),
    )


def _stage_message(stage: str) -> str:
    return {
        "loading_model": "Loading WhisperX model",
        "transcribing": "Transcribing live audio",
        "transcript_preview": "Live transcript preview ready",
        "aligning": "Aligning words to timestamps",
        "diarizing": "Detecting speaker turns",
        "assigning_speakers": "Assigning speakers to words",
        "completed": "WhisperX processing completed",
        "failed": "Live speaker processing unavailable",
    }.get(stage, stage.replace("_", " "))


def _live_runtime_metadata(settings, provider_name: str) -> dict:
    return {
        "provider": provider_name,
        "model": settings.whisperx_model if provider_name == "whisperx" else provider_name,
        "device": settings.whisperx_device,
        "compute_type": settings.whisperx_compute_type,
        "chunk_seconds": settings.live_chunk_seconds,
        "window_seconds": settings.live_window_seconds,
        "max_chunk_bytes": settings.live_max_chunk_bytes,
        "timeout_seconds": settings.live_processing_timeout_seconds,
        **_warmup_hint_metadata(provider_name),
        **_speaker_verification_live_metadata(settings, provider_name),
    }


def _slow_processing_message(provider_name: str) -> str:
    if provider_name == "whisperx":
        return "WhisperX is still processing; showing browser captions until speaker labels are ready"
    return "Audio uploaded; showing browser captions until speaker labels are ready"


def _live_degraded_metadata(provider_name: str, stage: str) -> dict:
    metadata = {
        "provider": provider_name,
        "degraded_stage": stage,
        **_warmup_hint_metadata(provider_name),
    }
    if stage == "processing_slow":
        metadata.update(
            {
                "caption_fallback": True,
                "correction_pending": True,
                "realtime_transcript_source": "browser_caption",
            }
        )
    return metadata


def _speaker_verification_live_metadata(settings, provider_name: str) -> dict:
    if provider_name != "whisperx":
        return {
            "speaker_verification_status": "not_required",
            "speaker_verification_ready": True,
            "speaker_verification_count": 0,
            "speaker_verification_labels": [],
            "speaker_verification_quality": "",
        }

    path = settings.speaker_verification_path.expanduser()
    base = {
        "speaker_verification_ready": False,
        "speaker_verification_count": 0,
        "speaker_verification_labels": [],
        "speaker_verification_quality": "",
    }
    if not path.exists():
        return {
            **base,
            "speaker_verification_status": "not_verified",
            "speaker_verification_detail": "No real diarization verification report has been recorded.",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            **base,
            "speaker_verification_status": "invalid",
            "speaker_verification_detail": "Speaker verification report is unreadable.",
        }

    labels = raw.get("speaker_labels") or []
    if not isinstance(labels, list):
        labels = []
    quality = raw.get("quality") or {}
    if not isinstance(quality, dict):
        quality = {}
    error = raw.get("error")
    verified = bool(raw.get("verified"))
    status = "verified" if verified else "failed" if error else "not_verified"
    count = int(raw.get("distinct_speaker_count") or len(labels) or 0)
    detail = str(raw.get("detail") or error or "")
    if not detail:
        detail = (
            f"Verified with {count} speaker label(s)."
            if verified
            else "Real diarization smoke has not passed yet."
        )
    return {
        **base,
        "speaker_verification_status": status,
        "speaker_verification_ready": verified,
        "speaker_verification_count": count,
        "speaker_verification_labels": [str(label) for label in labels[:6]],
        "speaker_verification_quality": str(quality.get("level") or ""),
        "speaker_verification_detail": detail,
    }


def _warmup_hint_metadata(provider_name: str) -> dict:
    if provider_name != "whisperx":
        return {"warmup_recommended": False}
    return {
        "warmup_recommended": True,
        "warmup_hint": "Run speaker warmup to load WhisperX and pyannote before live audio.",
    }




async def _maybe_call_agent(
    *,
    room_id: str,
    session_id: str,
    segments,
    user: UserPublic,
    collab: CollaborationService,
    settings: Settings,
    pipeline: VoiceAgentPipeline,
    wake_words: list[str],
    events: RoomEventHub,
) -> dict | None:
    router = MeetingCommandRouter(collab=collab, settings=settings, events=events)
    for segment in segments:
        prompt = _agent_prompt(segment.text, wake_words)
        if not prompt:
            continue
        read_only = router.answer_read_only_prompt(room_id, prompt, memory_question_mode="broad")
        if read_only:
            route_trace = router.trace_prompt(room_id, prompt, memory_question_mode="broad").model_dump(mode="json")
            metadata = {
                **read_only.metadata,
                "route_trace": route_trace,
                "trigger": "wake_word",
                "prompt": prompt,
                "speaker_label": segment.speaker_label,
                "identified_user_id": segment.identified_user_id,
                "identified_user_name": segment.identified_user_name,
            }
            collab.add_agent_message(
                room_id,
                read_only.text,
                metadata=metadata,
            )
            return {
                "text": read_only.text,
                "trigger": "wake_word",
                **read_only.metadata,
                "route_trace": route_trace,
                "speaker_label": segment.speaker_label,
            }
        resolved_context = collab.resolve_recent_task_context(room_id, prompt)
        context = {
            "room_id": room_id,
            "source": "live_meeting",
            "speaker_label": segment.speaker_label,
            "identified_user_id": segment.identified_user_id,
            "identified_user_name": segment.identified_user_name,
        }
        if resolved_context:
            context["resolved_context"] = resolved_context
        result = await pipeline.process_text(
            prompt,
            session_id=_scoped_session_id(room_id, user, f"{session_id}:agent"),
            incident_context=context,
            include_tts=False,
        )
        agent_metadata = router.handle_processed_result(result, room_id, memory_question_mode="broad") or {}
        collab.add_agent_message(
            room_id,
            result.response_text,
            metadata={
                "source": agent_metadata.get("source", "live_meeting"),
                "trigger": "wake_word",
                "prompt": prompt,
                "speaker_label": segment.speaker_label,
                "identified_user_id": segment.identified_user_id,
                "identified_user_name": segment.identified_user_name,
                **agent_metadata,
            },
        )
        if result.orchestrator_result:
            collab.add_action(room_id, user, result.orchestrator_result)
        return {
            "text": result.response_text,
            "trigger": "wake_word",
            **agent_metadata,
            "speaker_label": segment.speaker_label,
        }
    return None


def _agent_prompt(text: str, wake_words: list[str]) -> str | None:
    pattern = _wake_pattern(wake_words)
    cleaned = pattern.sub("", text or "", count=1).strip()
    if cleaned == (text or "").strip():
        return None
    return cleaned or text.strip()


def _wake_words(configured: str, display_name: str) -> list[str]:
    words = [word.strip() for word in configured.split(",") if word.strip()]
    if display_name.strip():
        words.append(display_name.strip())
    normalized = []
    seen = set()
    for word in words:
        key = word.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(word)
    return normalized or ["assistant", "agent"]


def _wake_pattern(wake_words: list[str]) -> re.Pattern:
    escaped = []
    for word in wake_words:
        parts = [re.escape(part) for part in word.split()]
        escaped.append(r"\s+".join(parts))
    alternatives = "|".join(sorted(escaped, key=len, reverse=True))
    return re.compile(
        rf"^\s*(?:hey|hi|ok|okay)?\s*(?:{alternatives})(?=$|[:,\s-])[:,\s-]*",
        re.IGNORECASE,
    )


def _websocket_user(websocket: WebSocket) -> UserPublic:
    token = websocket.query_params.get("token")
    if not token:
        raise RuntimeError("Missing WebSocket token")
    settings = _websocket_dependency(websocket, get_settings)
    store = _websocket_dependency(websocket, get_user_store)
    payload = decode_access_token(token, settings)
    user = store.get_by_id(payload.sub)
    if user is None:
        raise RuntimeError("User not found")
    return to_public_user(user)


def _websocket_dependency(websocket: WebSocket, dependency):
    provider = websocket.app.dependency_overrides.get(dependency, dependency)
    return provider()


def _json_payload(text: str) -> dict:
    import json

    return json.loads(text)


def _decode_audio_payload(value: str | None) -> bytes:
    if not value:
        return b""
    return base64.b64decode(value)


async def _send_json(websocket: WebSocket, runtime: LiveSocketRuntime, payload: dict) -> bool:
    if runtime.closed:
        return False
    async with runtime.send_lock:
        if runtime.closed:
            return False
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            runtime.closed = True
            return False
    return True


async def _send_status(
    websocket: WebSocket,
    runtime: LiveSocketRuntime,
    state: str,
    message: str,
    **extra,
) -> None:
    await _send_json(websocket, runtime, {"type": "session_status", "state": state, "message": message, **extra})


async def _send_error(
    websocket: WebSocket,
    runtime: LiveSocketRuntime,
    code: str,
    message: str,
    *,
    recoverable: bool = True,
) -> None:
    await _send_json(websocket, runtime, {"type": "error", "code": code, "message": message, "recoverable": recoverable})
