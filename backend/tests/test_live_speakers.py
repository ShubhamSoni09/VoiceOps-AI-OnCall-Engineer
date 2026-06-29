import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.dependencies import get_user_store
from app.auth.models import UserPublic
from app.auth.users import UserStore
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.system import router as system_router
from app.system.router import DemoGateRunResponse
from app.speakers.live import AudioChunk, LiveChunkRejected, LiveMeetingSession, RollingAudioBuffer
from app.speakers.provider import (
    MockSpeakerProvider,
    _replay_worker_stages,
    _worker_output_tail,
    segments_from_whisperx,
    transcript_preview_from_whisperx,
)
from app.speakers.service import SpeakerService, get_speaker_service
from app.speakers.store import SpeakerStore
import app.voice_agent.router as voice_router
from app.speakers.router import LiveSessionRegistry, _agent_prompt, _live_runtime_metadata, _wake_words
from app.voice_agent.models import OrchestratorResult


@pytest.fixture
def client(tmp_path):
    users_path = tmp_path / "users.json"
    store = UserStore(users_path)
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    speakers = SpeakerService(
        SpeakerStore(tmp_path / "speakers.json"),
        collab,
        MockSpeakerProvider(),
    )
    test_settings = Settings(
        users_store_path=users_path,
        jwt_secret="test-secret-key",
        memory_store_path=str(tmp_path / "memory.json"),
        collab_store_path=tmp_path / "collab-unused.json",
        speaker_store_path=tmp_path / "speakers-unused.json",
        speaker_provider="mock",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
        live_chunk_seconds=2.0,
        live_window_seconds=4.0,
        voiceops_workspace=None,
    )

    voice_router._pipeline = None
    app.dependency_overrides[get_user_store] = lambda: store
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_collaboration_service] = lambda: collab
    app.dependency_overrides[get_speaker_service] = lambda: speakers

    with TestClient(app) as test_client:
        yield test_client

    voice_router._pipeline = None
    app.dependency_overrides.clear()


def _token(client, email="priya@voiceops.dev", password="oncall123") -> str:
    res = client.post("/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def _join(client, token):
    res = client.post(
        "/collab/rooms/main/join",
        headers={"Authorization": f"Bearer {token}"},
        json={"room_name": "Team room", "project": "workspace"},
    )
    assert res.status_code == 200


def _receive_until(ws, predicate, *, limit=12):
    events = []
    for _ in range(limit):
        event = ws.receive_json()
        events.append(event)
        if predicate(event):
            return event, events
    raise AssertionError(f"expected live event was not received; saw {events}")


def test_rolling_audio_buffer_keeps_latest_window():
    buffer = RollingAudioBuffer(chunk_seconds=2.0, window_seconds=4.0)

    buffer.add(AudioChunk(sequence=1, data=b"one", mime_type="audio/webm"))
    buffer.add(AudioChunk(sequence=2, data=b"two", mime_type="audio/webm"))
    window = buffer.add(AudioChunk(sequence=3, data=b"three", mime_type="audio/webm"))

    assert window == b"twothree"
    assert buffer.latest_sequence == 3


def test_live_session_rejects_duplicate_out_of_order_empty_and_oversized_chunks():
    session = LiveMeetingSession(
        room_id="main",
        session_id="live-hardening",
        mime_type="audio/webm",
        buffer=RollingAudioBuffer(chunk_seconds=2.0, window_seconds=4.0),
        max_chunk_bytes=4,
    )

    sequence, window, stats = session.add_chunk(b"one", sequence=1)

    assert sequence == 1
    assert window == b"one"
    assert stats["trace_id"].startswith("live-")
    assert stats["session_id"] == "live-hardening"
    assert stats["accepted_chunks"] == 1
    assert stats["processed_chunks"] == 0
    assert stats["total_audio_bytes"] == 3
    assert stats["latest_window_bytes"] == 3
    assert stats["dropped_chunks"] == 0

    with pytest.raises(LiveChunkRejected) as duplicate:
        session.add_chunk(b"one", sequence=1)
    assert duplicate.value.reason == "duplicate_chunk"
    assert duplicate.value.stats["duplicate_chunks"] == 1

    session.add_chunk(b"two", sequence=3)
    with pytest.raises(LiveChunkRejected) as out_of_order:
        session.add_chunk(b"old", sequence=2)
    assert out_of_order.value.reason == "out_of_order_chunk"
    assert out_of_order.value.stats["out_of_order_chunks"] == 1
    assert out_of_order.value.stats["gap_chunks"] == 1

    with pytest.raises(LiveChunkRejected) as empty:
        session.add_chunk(b"", sequence=4)
    assert empty.value.reason == "empty_chunk"
    assert empty.value.stats["empty_chunks"] == 1

    with pytest.raises(LiveChunkRejected) as oversized:
        session.add_chunk(b"12345", sequence=5)
    assert oversized.value.reason == "oversized_chunk"
    assert oversized.value.stats["oversized_chunks"] == 1
    assert oversized.value.stats["dropped_chunks"] == 4


def test_live_session_tracking_sets_are_bounded_for_long_meetings():
    session = LiveMeetingSession(
        room_id="main",
        session_id="live-long",
        mime_type="audio/webm",
        buffer=RollingAudioBuffer(chunk_seconds=2.0, window_seconds=8.0),
        max_chunk_bytes=64,
    )

    for sequence in range(1, 650):
        session.add_chunk(b"ok", sequence=sequence)
        assert session.should_emit_partial(temp_id=f"live-long-{sequence}", text="ok", source="provider")
        assert session.should_emit_segments(
            temp_id=f"live-long-{sequence}",
            segment_payloads=[{"speaker_label": "SPEAKER_00", "text": str(sequence)}],
        )

    stats = session.stats()
    assert stats["accepted_chunks"] == 649
    assert stats["tracked_sequences"] <= 512
    assert stats["tracked_partials"] <= 512
    assert stats["tracked_results"] <= 512
    assert 649 in session.seen_sequences


def test_live_session_registry_removes_explicitly_stopped_sessions():
    registry = LiveSessionRegistry()
    session, resumed, restored = registry.start(
        room_id="main",
        session_id="live-stop",
        mime_type="audio/webm",
        chunk_seconds=2.0,
        window_seconds=8.0,
        max_chunk_bytes=1024,
    )

    assert resumed is False
    assert restored is False
    assert ("main", "live-stop") in registry.sessions

    registry.stop(session)

    assert ("main", "live-stop") not in registry.sessions


def test_live_session_registry_prunes_idle_resumable_sessions():
    registry = LiveSessionRegistry()
    stale, _, _ = registry.start(
        room_id="main",
        session_id="live-stale",
        mime_type="audio/webm",
        chunk_seconds=2.0,
        window_seconds=8.0,
        max_chunk_bytes=1024,
    )
    fresh, _, _ = registry.start(
        room_id="main",
        session_id="live-fresh",
        mime_type="audio/webm",
        chunk_seconds=2.0,
        window_seconds=8.0,
        max_chunk_bytes=1024,
    )
    stale.last_seen_at = 10.0
    fresh.last_seen_at = 99.0

    removed = registry.prune_idle(ttl_seconds=30.0, now=100.0)

    assert removed == 1
    assert ("main", "live-stale") not in registry.sessions
    assert ("main", "live-fresh") in registry.sessions


def test_live_resume_without_restored_session_rejects_last_confirmed_chunk():
    registry = LiveSessionRegistry()
    session, resumed, restored = registry.start(
        room_id="main",
        session_id="replacement-session",
        mime_type="audio/webm",
        chunk_seconds=2.0,
        window_seconds=4.0,
        max_chunk_bytes=64,
        resume_session_id="lost-live-session",
        last_sequence=7,
    )

    assert resumed is True
    assert restored is False
    assert session.latest_accepted_sequence == 7
    assert session.next_sequence == 8

    with pytest.raises(LiveChunkRejected) as duplicate:
        session.add_chunk(b"replayed boundary", sequence=7)

    assert duplicate.value.reason == "duplicate_chunk"
    assert duplicate.value.stats["duplicate_chunks"] == 1
    assert duplicate.value.stats["accepted_chunks"] == 0

    sequence, window, stats = session.add_chunk(b"next chunk", sequence=8)
    assert sequence == 8
    assert window == b"next chunk"
    assert stats["accepted_chunks"] == 1


def test_segments_from_whisperx_output():
    segments = segments_from_whisperx(
        [
            {"speaker": "SPEAKER_00", "text": " Hello ", "start": 0.5, "end": 1.25, "score": 0.91},
            {"speaker": "SPEAKER_01", "text": "Review it", "start": 1.3, "end": 2.0},
        ]
    )

    assert segments[0].speaker_label == "SPEAKER_00"
    assert segments[0].text == "Hello"
    assert segments[0].start_ms == 500
    assert segments[0].end_ms == 1250
    assert segments[0].confidence == 0.91
    assert segments[1].speaker_label == "SPEAKER_01"


def test_transcript_preview_from_whisperx_segments():
    preview = transcript_preview_from_whisperx(
        [
            {"text": " Alice found "},
            {"text": " the failing health check. "},
            {"text": ""},
        ]
    )

    assert preview == "Alice found the failing health check."


def test_worker_stage_replay_calls_stage_callback():
    seen = []

    _replay_worker_stages(
        'noise\nVOICEOPS_LIVE_STAGE={"stage":"diarizing","message":"Detecting speaker turns"}\n',
        lambda stage, message: seen.append((stage, message)),
    )

    assert seen == [("diarizing", "Detecting speaker turns")]


def test_worker_output_tail_includes_stdout_and_stderr():
    detail = _worker_output_tail(
        b"VOICEOPS_LIVE_STAGE={\"stage\":\"loading_model\"}\n",
        b"line1\nline2\n",
    )

    assert 'stdout:\nVOICEOPS_LIVE_STAGE={"stage":"loading_model"}' in detail
    assert "stderr:\nline1\nline2" in detail


def test_live_runtime_metadata_includes_verified_speaker_report(tmp_path):
    verification_path = tmp_path / "speaker_verification.json"
    verification_path.write_text(
        json.dumps(
            {
                "verified": True,
                "provider": "whisperx",
                "distinct_speaker_count": 2,
                "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
                "quality": {"level": "verified"},
                "detail": "Generated two-speaker verification completed",
                "transcript": "private transcript should not be forwarded",
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        speaker_provider="whisperx",
        speaker_verification_path=verification_path,
    )

    metadata = _live_runtime_metadata(settings, "whisperx")

    assert metadata["speaker_verification_status"] == "verified"
    assert metadata["speaker_verification_ready"] is True
    assert metadata["speaker_verification_count"] == 2
    assert metadata["speaker_verification_labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert metadata["speaker_verification_quality"] == "verified"
    assert "transcript" not in metadata


def test_live_runtime_metadata_handles_invalid_speaker_report(tmp_path):
    verification_path = tmp_path / "speaker_verification.json"
    verification_path.write_text("{not-json", encoding="utf-8")
    settings = Settings(
        speaker_provider="whisperx",
        speaker_verification_path=verification_path,
    )

    metadata = _live_runtime_metadata(settings, "whisperx")

    assert metadata["speaker_verification_status"] == "invalid"
    assert metadata["speaker_verification_ready"] is False
    assert metadata["speaker_verification_count"] == 0
    assert "unreadable" in metadata["speaker_verification_detail"]


@pytest.mark.asyncio
async def test_debug_live_chunk_bypasses_heavy_provider(tmp_path):
    class ExplodingProvider(MockSpeakerProvider):
        name = "whisperx"

        async def process_live_chunk(self, *args, **kwargs):
            raise AssertionError("debug live chunks should not invoke the heavy provider")

    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    service = SpeakerService(SpeakerStore(tmp_path / "speakers.json"), collab, ExplodingProvider())

    result = await service.process_live_chunk(
        "main",
        session_id="smoke",
        sequence=2,
        audio_bytes=b"not-real-audio",
        mime_type="audio/webm",
        debug_text="Bob is validating the live smoke path",
    )

    assert result.partial_text == "Bob is validating the live smoke path"
    assert result.segments[0].speaker_label == "SPEAKER_00"
    messages = collab.snapshot("main").messages
    assert messages[0].text == "Bob is validating the live smoke path"
    assert messages[0].metadata["live_temp_id"] == "smoke-2"


def test_custom_agent_wake_words_are_recognized():
    wake_words = _wake_words("nova,helper", "Ada")

    assert _agent_prompt("Ada what is open?", wake_words) == "what is open?"
    assert _agent_prompt("hey nova check status", wake_words) == "check status"
    assert _agent_prompt("helper, what files were mentioned?", wake_words) == "what files were mentioned?"
    assert _agent_prompt("regular teammate comment", wake_words) is None


def test_live_websocket_accepts_audio_chunks_and_persists_segments(client):
    token = _token(client)
    _join(client, token)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        assert ws.receive_json()["type"] == "session_status"
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-test",
                "mime_type": "audio/webm",
            }
        )
        started = ws.receive_json()
        assert started["type"] == "session_status"
        assert started["state"] == "listening"
        assert started["provider"] == "mock"
        assert started["trace_id"].startswith("live-")
        assert started["accepted_chunks"] == 0
        assert started["processed_chunks"] == 0
        assert started["device"] == "cpu"
        assert started["timeout_seconds"] == 8.0
        assert started["warmup_recommended"] is False

        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "Alice is checking the route",
            }
        )
        processing = ws.receive_json()
        partial = ws.receive_json()
        final = ws.receive_json()
        correction = ws.receive_json()
        listening = ws.receive_json()

        assert processing["state"] == "processing"
        assert processing["trace_id"] == started["trace_id"]
        assert processing["accepted_chunks"] == 1
        assert processing["processed_chunks"] == 0
        assert processing["total_audio_bytes"] == len(b"ignored")
        assert processing["latest_window_bytes"] == len(b"ignored")
        assert partial["type"] == "partial_transcript"
        assert partial["text"] == "Alice is checking the route"
        assert final["type"] == "speaker_segments"
        assert final["temp_id"] == partial["temp_id"]
        assert final["segments"][0]["speaker_label"] == "SPEAKER_01"
        assert correction["type"] == "speaker_correction"
        assert listening["state"] == "listening"
        assert listening["trace_id"] == started["trace_id"]
        assert listening["processed_chunks"] == 1
        assert listening["last_processing_ms"] >= 0
        assert listening["max_processing_ms"] >= listening["last_processing_ms"]

        ws.send_json({"type": "stop"})
        stopped = ws.receive_json()
        assert stopped["state"] == "stopped"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    assert snapshot.status_code == 200
    messages = snapshot.json()["messages"]
    assert messages[0]["source"] == "live_audio"
    assert messages[0]["text"] == "Alice is checking the route"
    assert messages[0]["speaker_label"] == "SPEAKER_01"
    assert messages[0]["metadata"]["live_temp_id"] == partial["temp_id"]


def test_live_websocket_requires_room_membership(client):
    admin = _token(client, "admin@voiceops.dev", "admin123")
    outsider = _token(client)
    _join(client, admin)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/speakers/rooms/main/live?token={outsider}"):
            pass

    assert exc.value.code == 1008


def test_live_websocket_drops_bad_chunks_without_duplicate_timeline_messages(client):
    token = _token(client)
    _join(client, token)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-drop-test",
                "mime_type": "audio/webm",
            }
        )
        started = ws.receive_json()
        assert started["max_chunk_bytes"] > 0

        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"first accepted chunk").decode("ascii"),
            }
        )
        listening, events = _receive_until(
            ws,
            lambda event: event.get("type") == "session_status" and event.get("stage") == "completed",
        )
        assert any(event.get("type") == "speaker_segments" for event in events)
        assert listening["dropped_chunks"] == 0

        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"duplicate ignored").decode("ascii"),
            }
        )
        duplicate = ws.receive_json()
        assert duplicate["state"] == "degraded"
        assert duplicate["stage"] == "duplicate_chunk"
        assert duplicate["duplicate_chunks"] == 1

        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 0,
                "audio_base64": base64.b64encode(b"older ignored").decode("ascii"),
            }
        )
        out_of_order = ws.receive_json()
        assert out_of_order["stage"] == "out_of_order_chunk"
        assert out_of_order["out_of_order_chunks"] == 1

        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 2,
                "audio_base64": "",
            }
        )
        empty = ws.receive_json()
        assert empty["stage"] == "empty_chunk"
        assert empty["empty_chunks"] == 1
        assert empty["dropped_chunks"] == 3

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    messages = snapshot.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["text"] == "first accepted chunk"


def test_live_websocket_can_resume_session_after_disconnect(client):
    token = _token(client)
    _join(client, token)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-resume-test",
                "mime_type": "audio/webm",
            }
        )
        started = ws.receive_json()
        assert started["resumed"] is False
        assert started["next_sequence"] == 1
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"before reconnect").decode("ascii"),
            }
        )
        _receive_until(
            ws,
            lambda event: event.get("type") == "session_status" and event.get("stage") == "completed",
        )

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "ignored-new-id",
                "resume_session_id": "live-resume-test",
                "last_sequence": 1,
                "mime_type": "audio/webm",
            }
        )
        resumed = ws.receive_json()
        assert resumed["state"] == "listening"
        assert resumed["resumed"] is True
        assert resumed["resume_restored"] is True
        assert resumed["session_id"] == "live-resume-test"
        assert resumed["next_sequence"] == 2
        assert resumed["reconnect_count"] == 1
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"duplicate after reconnect").decode("ascii"),
            }
        )
        duplicate = ws.receive_json()
        assert duplicate["stage"] == "duplicate_chunk"
        assert duplicate["duplicate_chunks"] == 1
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 2,
                "audio_base64": base64.b64encode(b"after reconnect").decode("ascii"),
            }
        )
        _receive_until(
            ws,
            lambda event: event.get("type") == "session_status" and event.get("stage") == "completed",
        )

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    messages = snapshot.json()["messages"]
    assert [message["text"] for message in messages] == ["before reconnect", "after reconnect"]
    assert [message["metadata"]["live_temp_id"] for message in messages] == [
        "live-resume-test-1",
        "live-resume-test-2",
    ]


def test_live_websocket_degrades_while_slow_provider_continues(client, tmp_path):
    class SlowProvider(MockSpeakerProvider):
        name = "whisperx"

        async def process_live_chunk(self, *args, **kwargs):
            await asyncio.sleep(0.03)
            return await super().process_live_chunk(*args, **kwargs)

    settings = app.dependency_overrides[get_settings]()
    original_timeout = settings.live_processing_timeout_seconds
    original_speaker_service = app.dependency_overrides[get_speaker_service]
    settings.live_processing_timeout_seconds = 0.001
    collab = app.dependency_overrides[get_collaboration_service]()
    app.dependency_overrides[get_speaker_service] = lambda: SpeakerService(
        SpeakerStore(tmp_path / "slow-speakers.json"),
        collab,
        SlowProvider(),
    )

    try:
        token = _token(client)
        _join(client, token)

        with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": "start",
                    "session_id": "live-slow-test",
                    "mime_type": "audio/webm",
                }
            )
            ws.receive_json()
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 1,
                    "audio_base64": base64.b64encode(b"slow provider transcript").decode("ascii"),
                }
            )

            processing = ws.receive_json()
            degraded = ws.receive_json()
            partial = ws.receive_json()
            final = ws.receive_json()
            correction = ws.receive_json()
            listening = ws.receive_json()

            assert processing["state"] == "processing"
            assert degraded["state"] == "degraded"
            assert "still processing" in degraded["message"]
            assert degraded["provider"] == "whisperx"
            assert degraded["caption_fallback"] is True
            assert degraded["correction_pending"] is True
            assert degraded["realtime_transcript_source"] == "browser_caption"
            assert degraded["warmup_recommended"] is True
            assert "speaker warmup" in degraded["warmup_hint"]
            assert degraded["sequence"] == 1
            assert partial["type"] == "partial_transcript"
            assert partial["text"] == "slow provider transcript"
            assert final["type"] == "speaker_segments"
            assert final["late_correction"] is True
            assert correction["type"] == "speaker_correction"
            assert correction["late_correction"] is True
            assert listening["state"] == "listening"
            assert listening["correction_pending"] is False
            assert listening["caption_fallback"] is False
            assert listening["late_correction"] is True
    finally:
        settings.live_processing_timeout_seconds = original_timeout
        app.dependency_overrides[get_speaker_service] = original_speaker_service


def test_live_websocket_queues_latest_chunk_while_provider_is_busy(client, tmp_path):
    class BusyProvider(MockSpeakerProvider):
        async def process_live_chunk(self, *args, **kwargs):
            await asyncio.sleep(0.08)
            return await super().process_live_chunk(*args, **kwargs)

    settings = app.dependency_overrides[get_settings]()
    original_timeout = settings.live_processing_timeout_seconds
    original_speaker_service = app.dependency_overrides[get_speaker_service]
    settings.live_processing_timeout_seconds = 0.001
    collab = app.dependency_overrides[get_collaboration_service]()
    app.dependency_overrides[get_speaker_service] = lambda: SpeakerService(
        SpeakerStore(tmp_path / "busy-speakers.json"),
        collab,
        BusyProvider(),
    )

    try:
        token = _token(client)
        _join(client, token)

        with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": "start",
                    "session_id": "live-busy-test",
                    "mime_type": "audio/webm",
                }
            )
            ws.receive_json()
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 1,
                    "audio_base64": base64.b64encode(b"slow first chunk").decode("ascii"),
                }
            )

            assert ws.receive_json()["stage"] == "received"
            slow = ws.receive_json()
            assert slow["stage"] == "processing_slow"

            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 2,
                    "audio_base64": base64.b64encode(b"second chunk should queue").decode("ascii"),
                }
            )
            assert ws.receive_json()["stage"] == "received"
            busy = ws.receive_json()
            assert busy["stage"] == "busy"
            assert busy["skipped_chunks"] == 1
            assert busy["queued_chunks"] == 1
            assert busy["pending_chunks"] == 1

            seen_partials = []
            seen_queued_processing = False
            for _ in range(12):
                event = ws.receive_json()
                if event.get("type") == "partial_transcript":
                    seen_partials.append(event["text"])
                if event.get("stage") == "queued_processing":
                    seen_queued_processing = True
                if "slow first chunk" in seen_partials and "second chunk should queue" in seen_partials:
                    break

            assert seen_queued_processing is True
            assert seen_partials[:2] == ["slow first chunk", "second chunk should queue"]
    finally:
        settings.live_processing_timeout_seconds = original_timeout
        app.dependency_overrides[get_speaker_service] = original_speaker_service

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    messages = snapshot.json()["messages"]
    assert [message["text"] for message in messages] == [
        "slow first chunk",
        "second chunk should queue",
    ]


def test_live_websocket_emits_provider_stage_status(client, tmp_path):
    class StageProvider(MockSpeakerProvider):
        async def process_live_chunk(self, *args, stage_callback=None, **kwargs):
            if stage_callback:
                stage_callback("loading_model", "Loading staged provider")
                stage_callback("diarizing", "Detecting staged speakers")
            return await super().process_live_chunk(*args, **kwargs)

    original_speaker_service = app.dependency_overrides[get_speaker_service]
    collab = app.dependency_overrides[get_collaboration_service]()
    app.dependency_overrides[get_speaker_service] = lambda: SpeakerService(
        SpeakerStore(tmp_path / "stage-speakers.json"),
        collab,
        StageProvider(),
    )

    try:
        token = _token(client)
        _join(client, token)

        with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": "start",
                    "session_id": "live-stage-test",
                    "mime_type": "audio/webm",
                }
            )
            ws.receive_json()
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 1,
                    "audio_base64": base64.b64encode(b"stage provider transcript").decode("ascii"),
                }
            )

            received = ws.receive_json()
            loading = ws.receive_json()
            diarizing = ws.receive_json()
            partial = ws.receive_json()

            assert received["stage"] == "received"
            assert loading["type"] == "session_status"
            assert loading["stage"] == "loading_model"
            assert loading["elapsed_ms"] >= 0
            assert diarizing["stage"] == "diarizing"
            assert partial["type"] == "partial_transcript"
    finally:
        app.dependency_overrides[get_speaker_service] = original_speaker_service


def test_live_websocket_suppresses_duplicate_asr_preview(client, tmp_path):
    class DuplicatePreviewProvider(MockSpeakerProvider):
        name = "whisperx"

        async def process_live_chunk(self, *args, stage_callback=None, **kwargs):
            if stage_callback:
                stage_callback("transcript_preview", "same provisional text")
                stage_callback("transcript_preview", "same provisional text")
                stage_callback("diarizing", "Detecting speaker turns")
            await asyncio.sleep(0)
            return await super().process_live_chunk(*args, **kwargs)

    original_speaker_service = app.dependency_overrides[get_speaker_service]
    collab = app.dependency_overrides[get_collaboration_service]()
    app.dependency_overrides[get_speaker_service] = lambda: SpeakerService(
        SpeakerStore(tmp_path / "duplicate-preview-speakers.json"),
        collab,
        DuplicatePreviewProvider(),
    )

    try:
        token = _token(client)
        _join(client, token)

        with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": "start",
                    "session_id": "live-duplicate-preview-test",
                    "mime_type": "audio/webm",
                }
            )
            ws.receive_json()
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 1,
                    "audio_base64": base64.b64encode(b"final duplicate preview text").decode("ascii"),
                }
            )

            assert ws.receive_json()["stage"] == "received"
            preview = ws.receive_json()
            assert preview["type"] == "partial_transcript"
            assert preview["text"] == "same provisional text"
            diarizing = ws.receive_json()
            assert diarizing["stage"] == "diarizing"
            final_partial = ws.receive_json()
            final = ws.receive_json()
            correction = ws.receive_json()
            listening = ws.receive_json()

            assert final_partial["type"] == "partial_transcript"
            assert final["type"] == "speaker_segments"
            assert correction["type"] == "speaker_correction"
            assert listening["state"] == "listening"
            assert listening["duplicate_partials"] == 1
    finally:
        app.dependency_overrides[get_speaker_service] = original_speaker_service


def test_live_websocket_emits_asr_preview_before_speaker_segments(client, tmp_path):
    class PreviewProvider(MockSpeakerProvider):
        name = "whisperx"

        async def process_live_chunk(self, *args, stage_callback=None, **kwargs):
            if stage_callback:
                stage_callback("transcribing", "Transcribing live audio")
                stage_callback("transcript_preview", "ASR text before diarization")
                stage_callback("diarizing", "Detecting speaker turns")
            await asyncio.sleep(0)
            return await super().process_live_chunk(*args, **kwargs)

    original_speaker_service = app.dependency_overrides[get_speaker_service]
    collab = app.dependency_overrides[get_collaboration_service]()
    app.dependency_overrides[get_speaker_service] = lambda: SpeakerService(
        SpeakerStore(tmp_path / "preview-speakers.json"),
        collab,
        PreviewProvider(),
    )

    try:
        token = _token(client)
        _join(client, token)

        with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "type": "start",
                    "session_id": "live-preview-test",
                    "mime_type": "audio/webm",
                }
            )
            ws.receive_json()
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 1,
                    "audio_base64": base64.b64encode(b"final diarized text").decode("ascii"),
                }
            )

            assert ws.receive_json()["stage"] == "received"
            assert ws.receive_json()["stage"] == "transcribing"
            preview = ws.receive_json()
            assert preview["type"] == "partial_transcript"
            assert preview["temp_id"] == "live-preview-test-1"
            assert preview["text"] == "ASR text before diarization"
            assert preview["provisional"] is True
            assert preview["source"] == "asr_preview"
            assert ws.receive_json()["stage"] == "diarizing"
            final_partial = ws.receive_json()
            final = ws.receive_json()
            assert final_partial["type"] == "partial_transcript"
            assert final_partial["text"] == "final diarized text"
            assert final["type"] == "speaker_segments"
    finally:
        app.dependency_overrides[get_speaker_service] = original_speaker_service


def test_live_websocket_disconnect_without_stop_is_clean(client):
    token = _token(client)
    _join(client, token)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        assert ws.receive_json()["type"] == "session_status"
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-disconnect-test",
                "mime_type": "audio/webm",
            }
        )
        assert ws.receive_json()["state"] == "listening"


def test_live_websocket_reports_bad_event_status(client):
    token = _token(client)
    _join(client, token)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_text("{")

        degraded = ws.receive_json()
        error = ws.receive_json()

        assert degraded["type"] == "session_status"
        assert degraded["state"] == "degraded"
        assert degraded["stage"] == "bad_event"
        assert error["type"] == "error"
        assert error["code"] == "bad_event"
        assert error["recoverable"] is True


def test_live_wake_word_lets_agent_join_meeting(client):
    token = _token(client)
    _join(client, token)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-agent-test",
                "mime_type": "audio/webm",
            }
        )
        ws.receive_json()
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "VoiceOps check status",
            }
        )

        assert ws.receive_json()["state"] == "processing"
        assert ws.receive_json()["type"] == "partial_transcript"
        assert ws.receive_json()["type"] == "speaker_segments"
        assert ws.receive_json()["type"] == "speaker_correction"
        agent = ws.receive_json()
        listening = ws.receive_json()

        assert agent["type"] == "agent_response"
        assert agent["trigger"] == "wake_word"
        assert "status" in agent["text"].lower()
        assert listening["state"] == "listening"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["state"] == "stopped"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    messages = snapshot.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "agent"]
    assert messages[0]["text"] == "VoiceOps check status"
    assert messages[1]["actor_name"] == "VoiceOps"


def test_live_agent_sessions_are_scoped_by_user(client):
    priya = _token(client)
    admin = _token(client, "admin@voiceops.dev", "admin123")
    _join(client, priya)

    def send_prompt(token):
        with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
            ws.receive_json()
            ws.send_json({"type": "start", "session_id": "shared-live", "mime_type": "audio/webm"})
            ws.receive_json()
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": 1,
                    "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                    "debug_text": "VoiceOps fix the health check",
                }
            )
            for _ in range(6):
                if ws.receive_json().get("type") == "agent_response":
                    break
            ws.send_json({"type": "stop"})
            for _ in range(3):
                if ws.receive_json().get("state") == "stopped":
                    break

    send_prompt(priya)
    send_prompt(admin)

    pipeline = voice_router._pipeline
    assert pipeline is not None
    assert pipeline._memory.get_history("shared-live:agent") == []
    assert pipeline._memory.get_history("main:user-priya:shared-live:agent")
    assert pipeline._memory.get_history("main:user-admin:shared-live:agent")


def test_live_wake_word_uses_saved_agent_settings(client):
    token = _token(client, "admin@voiceops.dev", "admin123")
    _join(client, token)
    update = client.put(
        "/collab/rooms/main/agent-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "display_name": "Ada",
            "initials": "AD",
            "wake_words": ["ada"],
        },
    )
    assert update.status_code == 200

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-custom-agent-test",
                "mime_type": "audio/webm",
            }
        )
        ws.receive_json()
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "Ada check status",
            }
        )

        assert ws.receive_json()["state"] == "processing"
        assert ws.receive_json()["type"] == "partial_transcript"
        assert ws.receive_json()["type"] == "speaker_segments"
        assert ws.receive_json()["type"] == "speaker_correction"
        agent = ws.receive_json()
        assert agent["type"] == "agent_response"
        assert agent["trigger"] == "wake_word"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["state"] == "listening"
        assert ws.receive_json()["state"] == "stopped"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    assert snapshot.json()["messages"][-1]["actor_name"] == "Ada"


def test_live_wake_word_memory_question_uses_room_memory(client):
    token = _token(client)
    _join(client, token)
    message = client.post(
        "/collab/rooms/main/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "text": "We decided to keep the live memory query deterministic",
            "source": "meeting_audio",
        },
    )
    assert message.status_code == 200

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-memory-test",
                "mime_type": "audio/webm",
            }
        )
        ws.receive_json()
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "VoiceOps what did we decide?",
            }
        )

        assert ws.receive_json()["state"] == "processing"
        assert ws.receive_json()["type"] == "partial_transcript"
        assert ws.receive_json()["type"] == "speaker_segments"
        assert ws.receive_json()["type"] == "speaker_correction"
        agent = ws.receive_json()
        assert agent["type"] == "agent_response"
        assert agent["source"] == "rag_query"
        assert "live memory query deterministic" in agent["text"]
        assert ws.receive_json()["state"] == "listening"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["state"] == "stopped"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    room = snapshot.json()
    assert room["actions"] == []
    assert room["messages"][-1]["metadata"]["source"] == "rag_query"
    assert room["messages"][-1]["metadata"]["matched_items"]


def test_live_wake_word_audit_question_uses_action_audit(client):
    token = _token(client)
    _join(client, token)
    collab = app.dependency_overrides[get_collaboration_service]()
    requester = UserPublic(
        id="user-priya",
        email="priya@voiceops.dev",
        name="Priya Nair",
        initials="PN",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
    action = collab.add_action(
        "main",
        requester,
        OrchestratorResult(
            executed=True,
            action="patch",
            summary="Patch proposal for app.py.",
            files_changed=["app.py"],
            command_output="tests passed",
            approval={
                "status": "approved",
                "decided_by_name": "Sam Ortiz",
                "git": {"branch_name": "voiceops/act-live-audit-fix-health", "files_changed": ["app.py"]},
            },
        ),
    )
    assert action is not None

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-audit-test",
                "mime_type": "audio/webm",
            }
        )
        ws.receive_json()
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "VoiceOps who approved the patch?",
            }
        )

        assert ws.receive_json()["state"] == "processing"
        assert ws.receive_json()["type"] == "partial_transcript"
        assert ws.receive_json()["type"] == "speaker_segments"
        assert ws.receive_json()["type"] == "speaker_correction"
        agent = ws.receive_json()
        assert agent["type"] == "agent_response"
        assert agent["source"] == "audit_query"
        assert "Sam Ortiz approved patch proposal" in agent["text"]
        assert "voiceops/act-live-audit-fix-health" in agent["text"]
        assert agent["route_trace"]["route"] == "audit_query"
        assert agent["route_trace"]["read_only"] is True
        assert ws.receive_json()["state"] == "listening"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["state"] == "stopped"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    room = snapshot.json()
    assert room["messages"][-1]["metadata"]["source"] == "audit_query"
    assert action.id in room["messages"][-1]["metadata"]["matched_items"][0]
    assert room["messages"][-1]["metadata"]["route_trace"]["route"] == "audit_query"


def test_live_wake_word_change_question_uses_change_summary(client):
    token = _token(client)
    _join(client, token)
    message = client.post(
        "/collab/rooms/main/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "text": "Need to update app.py before the handoff",
            "source": "meeting_audio",
        },
    )
    assert message.status_code == 200

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-change-summary-test",
                "mime_type": "audio/webm",
            }
        )
        ws.receive_json()
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "VoiceOps what changes did Priya make?",
            }
        )

        assert ws.receive_json()["state"] == "processing"
        assert ws.receive_json()["type"] == "partial_transcript"
        assert ws.receive_json()["type"] == "speaker_segments"
        assert ws.receive_json()["type"] == "speaker_correction"
        agent = ws.receive_json()
        assert agent["type"] == "agent_response"
        assert agent["source"] == "change_summary"
        assert "app.py" in agent["text"]
        assert ws.receive_json()["state"] == "listening"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["state"] == "stopped"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    assert snapshot.json()["messages"][-1]["metadata"]["source"] == "change_summary"


def test_live_wake_word_can_start_real_browser_demo_gate(client, monkeypatch):
    token = _token(client)
    _join(client, token)
    calls = []

    def fake_start_gate(gate_id, _settings):
        calls.append(gate_id)
        return DemoGateRunResponse(
            job_id="gate-browser-123",
            gate_id=gate_id,
            label="Real browser mic live",
            state="running",
            poll_url="/system/demo/gates/runs/current",
        )

    monkeypatch.setattr(system_router, "start_demo_gate_run_job", fake_start_gate)

    with client.websocket_connect(f"/speakers/rooms/main/live?token={token}") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "start",
                "session_id": "live-demo-gate-test",
                "mime_type": "audio/webm",
            }
        )
        ws.receive_json()
        ws.send_json(
            {
                "type": "audio_chunk",
                "sequence": 1,
                "audio_base64": base64.b64encode(b"ignored").decode("ascii"),
                "debug_text": "VoiceOps run real browser gate",
            }
        )

        assert ws.receive_json()["state"] == "processing"
        assert ws.receive_json()["type"] == "partial_transcript"
        assert ws.receive_json()["type"] == "speaker_segments"
        assert ws.receive_json()["type"] == "speaker_correction"
        agent = ws.receive_json()
        assert agent["type"] == "agent_response"
        assert agent["source"] == "demo_gate_run"
        assert agent["gate_id"] == "real_browser_live"
        assert "gate-browser-123" in agent["text"]
        assert ws.receive_json()["state"] == "listening"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["state"] == "stopped"

    assert calls == ["real_browser_live"]
    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    room = snapshot.json()
    assert room["messages"][-1]["metadata"]["source"] == "demo_gate_run"
    assert room["messages"][-1]["metadata"]["gate_job_id"] == "gate-browser-123"
    assert room["actions"][-1]["action"] == "verify"
    assert room["actions"][-1]["approval"]["gate_id"] == "real_browser_live"
