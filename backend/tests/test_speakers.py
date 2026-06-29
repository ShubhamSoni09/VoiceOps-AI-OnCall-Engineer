import asyncio
import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.models import JoinRoomRequest
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.speakers.models import (
    LiveProviderResult,
    MappingSource,
    SpeakerMapping,
    SpeakerSegment,
    VoiceProfile,
    VoiceProfileStatus,
)
from app.speakers.migration import migrate_json_to_sqlite
from app.speakers.provider import (
    MockSpeakerProvider,
    PersistentWhisperXWorker,
    WhisperXSpeakerProvider,
    get_speaker_provider,
    _normalize_worker_mode,
    _replay_worker_stage_line,
    _use_subprocess_worker,
    _worker_output_tail_from_lines,
    _worker_settings_payload,
)
from app.speakers.service import SpeakerService, get_speaker_service
from app.speakers.sqlite_store import SQLiteSpeakerStore
from app.speakers.store import SpeakerStore, create_speaker_store, utc_now
import app.voice_agent.router as voice_router


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
        rag_index_path=tmp_path / "rag-index.json",
        speaker_store_path=tmp_path / "speakers-unused.json",
        speaker_provider="mock",
        llm_provider="mock",
        tts_provider="mock",
        stt_provider="mock",
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


def _token(client, email, password) -> str:
    res = client.post("/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def _join(client, token, room_id="main"):
    res = client.post(
        f"/collab/rooms/{room_id}/join",
        headers={"Authorization": f"Bearer {token}"},
        json={"room_name": "Team room", "project": "workspace"},
    )
    assert res.status_code == 200
    return res


def test_mock_provider_normalizes_segments():
    provider = MockSpeakerProvider()

    segments = provider.normalize_segments(
        [SpeakerSegment(speaker_label="", text="  check api logs  ")]
    )

    assert segments[0].speaker_label == "SPEAKER_00"
    assert segments[0].text == "check api logs"
    assert segments[0].confidence == 0.72


def test_whisperx_worker_payload_carries_runtime_settings():
    settings = Settings(
        hf_token="hf_test_token",
        whisperx_model="tiny",
        whisperx_device="cpu",
        whisperx_compute_type="int8",
        whisperx_worker_timeout_seconds=12,
    )

    payload = _worker_settings_payload(settings)

    assert payload == {
        "hf_token": "hf_test_token",
        "whisperx_model": "tiny",
        "whisperx_device": "cpu",
        "whisperx_compute_type": "int8",
        "whisperx_num_speakers": None,
        "whisperx_min_speakers": None,
        "whisperx_max_speakers": None,
        "whisperx_worker_timeout_seconds": 12,
        "whisperx_worker_mode": "subprocess",
    }


def test_whisperx_worker_payload_carries_speaker_hints():
    settings = Settings(
        hf_token="hf_test_token",
        whisperx_num_speakers=None,
        whisperx_min_speakers=2,
        whisperx_max_speakers=3,
    )

    payload = _worker_settings_payload(settings)

    assert payload["whisperx_min_speakers"] == 2
    assert payload["whisperx_max_speakers"] == 3


def test_whisperx_provider_factory_supports_in_process_worker_mode():
    settings = Settings(
        speaker_provider="whisperx",
        whisperx_worker_mode="in_process",
    )

    provider = get_speaker_provider("whisperx", settings)

    assert isinstance(provider, WhisperXSpeakerProvider)
    assert _use_subprocess_worker(settings) is False
    assert provider._use_subprocess is False


def test_whisperx_provider_factory_supports_persistent_subprocess_worker_mode():
    settings = Settings(
        speaker_provider="whisperx",
        whisperx_worker_mode="persistent_subprocess",
    )

    provider = get_speaker_provider("whisperx", settings)

    assert isinstance(provider, WhisperXSpeakerProvider)
    assert provider._worker_mode == "persistent_subprocess"
    assert provider._use_subprocess is False
    assert _use_subprocess_worker(settings) is False


def test_whisperx_worker_mode_defaults_unknown_values_to_subprocess():
    assert _normalize_worker_mode("persistent_subprocess") == "persistent_subprocess"
    assert _normalize_worker_mode("in_process") == "in_process"
    assert _normalize_worker_mode("unknown") == "subprocess"


def test_whisperx_worker_stage_line_replays_callback():
    events = []

    _replay_worker_stage_line(
        'VOICEOPS_LIVE_STAGE={"stage":"diarizing","message":"Detecting speaker turns"}',
        lambda stage, message: events.append((stage, message)),
    )

    assert events == [("diarizing", "Detecting speaker turns")]


def test_whisperx_worker_output_tail_from_streamed_lines():
    detail = _worker_output_tail_from_lines(
        ["stage 1", "stage 2"],
        ["warning", "error"],
        max_lines=1,
    )

    assert "stdout:\nstage 2" in detail
    assert "stderr:\nerror" in detail


def test_whisperx_sync_provider_emits_transcript_preview_before_diarization(monkeypatch):
    events = []
    call_order = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            call_order.append("transcribe")
            assert audio == "audio"
            assert batch_size == 4
            return {
                "language": "en",
                "segments": [
                    {"text": " Alice found "},
                    {"text": " the failing health check. "},
                ],
            }

    class FakeDiarizationPipeline:
        def __init__(self, token, device):
            call_order.append("load_diarization")
            assert token == "hf_test_token"
            assert device == "cpu"

        def __call__(self, audio, **kwargs):
            call_order.append("run_diarization")
            assert audio == "audio"
            assert kwargs == {"min_speakers": 2, "max_speakers": 2}
            return "diarized"

    fake_whisperx = types.ModuleType("whisperx")
    fake_whisperx.load_audio = lambda _path: "audio"
    fake_whisperx.load_model = lambda *_args, **_kwargs: FakeModel()
    fake_whisperx.load_align_model = lambda **_kwargs: ("align-model", "align-metadata")
    fake_whisperx.align = lambda segments, *_args, **_kwargs: {"segments": segments}
    fake_whisperx.assign_word_speakers = lambda _diarized, _aligned: {
        "segments": [
            {
                "speaker": "SPEAKER_00",
                "text": "Alice found the failing health check.",
                "start": 0,
                "end": 2,
                "score": 0.87,
            }
        ]
    }
    fake_diarize = types.ModuleType("whisperx.diarize")
    fake_diarize.DiarizationPipeline = FakeDiarizationPipeline
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setitem(sys.modules, "whisperx.diarize", fake_diarize)

    provider = WhisperXSpeakerProvider(
        Settings(
            hf_token="hf_test_token",
            whisperx_model="tiny",
            whisperx_device="cpu",
            whisperx_compute_type="int8",
            whisperx_min_speakers=2,
            whisperx_max_speakers=2,
        ),
        use_subprocess=False,
    )

    result = provider._process_live_chunk_sync(
        b"fake-wav",
        session_id="live-preview",
        sequence=1,
        mime_type="audio/wav",
        stage_callback=lambda stage, message: events.append((stage, message)),
    )

    assert ("transcript_preview", "Alice found the failing health check.") in events
    assert ("diarizing", "Detecting speaker turns with 2..2 speaker hint") in events
    assert events.index(("transcript_preview", "Alice found the failing health check.")) < events.index(
        ("diarizing", "Detecting speaker turns with 2..2 speaker hint")
    )
    assert call_order.index("load_diarization") < call_order.index("transcribe")
    assert call_order.index("transcribe") < call_order.index("run_diarization")
    assert result.partial_text == "Alice found the failing health check."
    assert result.segments[0].speaker_label == "SPEAKER_00"


@pytest.mark.asyncio
async def test_whisperx_in_process_warmup_loads_reusable_models(monkeypatch):
    events = []
    load_counts = {"asr": 0, "diarization": 0}

    class FakeDiarizationPipeline:
        def __init__(self, token, device):
            assert token == "hf_test_token"
            assert device == "cpu"
            load_counts["diarization"] += 1

    fake_whisperx = types.ModuleType("whisperx")

    def load_model(*_args, **_kwargs):
        load_counts["asr"] += 1
        return object()

    fake_whisperx.load_model = load_model
    fake_diarize = types.ModuleType("whisperx.diarize")
    fake_diarize.DiarizationPipeline = FakeDiarizationPipeline
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setitem(sys.modules, "whisperx.diarize", fake_diarize)

    provider = WhisperXSpeakerProvider(
        Settings(
            hf_token="hf_test_token",
            whisperx_model="tiny",
            whisperx_device="cpu",
            whisperx_compute_type="int8",
        ),
        use_subprocess=False,
    )

    await provider.warmup(stage_callback=lambda stage, message: events.append((stage, message)))
    await provider.warmup(stage_callback=lambda stage, message: events.append((stage, message)))

    assert load_counts == {"asr": 1, "diarization": 1}
    assert ("completed", "WhisperX in-process provider warmup completed") in events
    assert provider._model is not None
    assert provider._diarize_model is not None


@pytest.mark.asyncio
async def test_persistent_whisperx_worker_replays_stage_and_returns_result(monkeypatch):
    result = LiveProviderResult(
        temp_id="persistent-live-1",
        partial_text="Alice speaks",
        start_ms=0,
        end_ms=1000,
        segments=[SpeakerSegment(speaker_label="SPEAKER_00", text="Alice speaks")],
    )
    fake_process = _FakePersistentProcess(
        [
            'VOICEOPS_LIVE_STAGE={"stage":"transcribing","message":"fake transcribe"}\n',
            f"VOICEOPS_LIVE_RESULT={result.model_dump_json()}\n",
        ]
    )

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    worker = PersistentWhisperXWorker(Settings(whisperx_worker_timeout_seconds=1))
    events = []

    returned = await worker.process_live_chunk(
        b"audio",
        session_id="persistent-live",
        sequence=1,
        mime_type="audio/wav",
        stage_callback=lambda stage, message: events.append((stage, message)),
    )

    assert returned.partial_text == "Alice speaks"
    assert returned.segments[0].speaker_label == "SPEAKER_00"
    assert ("starting_worker", "Starting persistent WhisperX worker") in events
    assert ("transcribing", "fake transcribe") in events
    assert ("completed", "Persistent WhisperX worker completed") in events
    assert fake_process.stdin.writes
    await worker.aclose()


@pytest.mark.asyncio
async def test_persistent_whisperx_worker_warmup_waits_for_ready(monkeypatch):
    fake_process = _FakePersistentProcess(
        [
            'VOICEOPS_LIVE_STAGE={"stage":"loading_asr","message":"fake model"}\n',
            'VOICEOPS_LIVE_READY={"ready":true,"warmed":true}\n',
        ]
    )

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    worker = PersistentWhisperXWorker(Settings(whisperx_worker_timeout_seconds=1))
    events = []

    await worker.warmup(stage_callback=lambda stage, message: events.append((stage, message)))

    assert ("loading_asr", "fake model") in events
    assert ("completed", "Persistent WhisperX warmup completed") in events
    assert b'"type": "warmup"' in fake_process.stdin.writes[0]
    await worker.aclose()


@pytest.mark.asyncio
async def test_persistent_whisperx_worker_timeout_kills_process(monkeypatch):
    fake_process = _FakePersistentProcess([])

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return fake_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    worker = PersistentWhisperXWorker(Settings(whisperx_worker_timeout_seconds=0.01))

    with pytest.raises(RuntimeError, match="Persistent WhisperX worker timed out"):
        await worker.process_live_chunk(
            b"audio",
            session_id="persistent-timeout",
            sequence=1,
            mime_type="audio/wav",
        )

    assert fake_process.killed is True


class _FakePersistentProcess:
    def __init__(self, stdout_lines: list[str]) -> None:
        self.stdout = _FakeStdout(stdout_lines)
        self.stderr = _FakeStdout([])
        self.stdin = _FakeStdin()
        self.returncode = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.close()
        self.stderr.close()

    async def wait(self) -> int:
        return self.returncode or 0


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = False
        for line in lines:
            self._queue.put_nowait(line.encode("utf-8"))

    async def readline(self) -> bytes:
        if self._closed:
            return b""
        return await self._queue.get()

    def close(self) -> None:
        self._closed = True
        self._queue.put_nowait(b"")


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, value: bytes) -> None:
        self.writes.append(value)

    async def drain(self) -> None:
        return None


def test_sqlite_speaker_store_persists_profiles_and_mappings(tmp_path):
    db_path = tmp_path / "speakers.sqlite3"
    now = utc_now()
    store = SQLiteSpeakerStore(db_path)

    store.upsert_profile(
        VoiceProfile(
            user_id="user-bob",
            display_name="Bob",
            sample_count=2,
            status=VoiceProfileStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_mapping(
        SpeakerMapping(
            room_id="main",
            speaker_label="SPEAKER_00",
            user_id="user-bob",
            user_name="Bob",
            confidence=0.91,
            source=MappingSource.MANUAL,
            created_at=now,
            updated_at=now,
        )
    )

    reopened = SQLiteSpeakerStore(db_path)

    mapping = reopened.get_mapping("main", "SPEAKER_00")
    profile = reopened.get_profile("user-bob")
    assert mapping is not None
    assert mapping.user_name == "Bob"
    assert mapping.confidence == 0.91
    assert profile is not None
    assert profile.display_name == "Bob"
    assert reopened.list_mappings("main")[0].speaker_label == "SPEAKER_00"
    assert reopened.list_profiles()[0].user_id == "user-bob"


def test_json_speaker_store_writes_private_file(tmp_path):
    path = tmp_path / "speakers.json"
    now = utc_now()
    store = SpeakerStore(path)

    store.upsert_mapping(
        SpeakerMapping(
            room_id="main",
            speaker_label="SPEAKER_00",
            user_id="user-bob",
            user_name="Bob",
            confidence=0.91,
            source=MappingSource.MANUAL,
            created_at=now,
            updated_at=now,
        )
    )

    assert path.stat().st_mode & 0o777 == 0o600


def test_speaker_store_factory_selects_sqlite(tmp_path):
    settings = Settings(
        speaker_store_backend="sqlite",
        speaker_sqlite_path=tmp_path / "speakers.sqlite3",
    )

    store = create_speaker_store(settings)

    assert isinstance(store, SQLiteSpeakerStore)


def test_migrate_json_speaker_store_to_sqlite(tmp_path):
    json_path = tmp_path / "speakers.json"
    sqlite_path = tmp_path / "speakers.sqlite3"
    now = utc_now()
    source = SpeakerStore(json_path)
    source.upsert_profile(
        VoiceProfile(
            user_id="user-alice",
            display_name="Alice",
            sample_count=3,
            status=VoiceProfileStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
    )
    source.upsert_mapping(
        SpeakerMapping(
            room_id="main",
            speaker_label="SPEAKER_01",
            user_id="user-alice",
            user_name="Alice",
            confidence=0.88,
            source=MappingSource.MANUAL,
            created_at=now,
            updated_at=now,
        )
    )

    result = migrate_json_to_sqlite(json_path, sqlite_path)
    target = SQLiteSpeakerStore(sqlite_path)

    mapping = target.get_mapping("main", "SPEAKER_01")
    profile = target.get_profile("user-alice")
    assert result["profiles"] == 1
    assert result["mappings"] == 1
    assert mapping is not None
    assert mapping.user_name == "Alice"
    assert mapping.confidence == 0.88
    assert profile is not None
    assert profile.sample_count == 3


def test_migrate_json_speaker_store_refuses_existing_sqlite_without_replace(tmp_path):
    json_path = tmp_path / "speakers.json"
    sqlite_path = tmp_path / "speakers.sqlite3"
    source = SpeakerStore(json_path)
    now = utc_now()
    source.upsert_profile(
        VoiceProfile(
            user_id="user-alice",
            display_name="Alice",
            created_at=now,
            updated_at=now,
        )
    )
    SQLiteSpeakerStore(sqlite_path)

    with pytest.raises(FileExistsError):
        migrate_json_to_sqlite(json_path, sqlite_path)


def test_ingest_unknown_speaker_segments_and_room_state(client):
    token = _token(client, "priya@voiceops.dev", "oncall123")
    _join(client, token)

    res = client.post(
        "/speakers/rooms/main/segments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "meeting-1",
            "source": "meeting_audio",
            "segments": [
                {
                    "speaker_label": "SPEAKER_00",
                    "text": "I can check the failing tests",
                    "start_ms": 0,
                    "end_ms": 1200,
                    "confidence": 0.81,
                },
                {
                    "speaker_label": "SPEAKER_01",
                    "text": "Please inspect the API route",
                    "start_ms": 1300,
                    "end_ms": 2400,
                    "confidence": 0.69,
                },
            ],
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["message_count"] == 2
    assert payload["segments"][0]["identity_source"] == "mock"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {token}"})
    messages = snapshot.json()["messages"]
    assert messages[0]["actor_name"] == "Unknown speaker"
    assert messages[0]["speaker_label"] == "SPEAKER_00"
    assert messages[0]["confidence"] == 0.81
    assert messages[0]["metadata"]["identity_source"] == "mock"

    state = client.get("/speakers/rooms/main", headers={"Authorization": f"Bearer {token}"})
    assert state.status_code == 200
    unknown = {item["speaker_label"] for item in state.json()["unknown_speakers"]}
    assert unknown == {"SPEAKER_00", "SPEAKER_01"}


def test_mapping_applies_to_future_segments_and_handoff(client):
    alice = _token(client, "priya@voiceops.dev", "oncall123")
    bob = _token(client, "admin@voiceops.dev", "admin123")
    _join(client, alice)
    _join(client, bob)

    mapping = client.post(
        "/speakers/rooms/main/mappings",
        headers={"Authorization": f"Bearer {alice}"},
        json={
            "speaker_label": "SPEAKER_00",
            "user_id": "user-priya",
            "confidence": 0.95,
            "source": "manual",
        },
    )
    assert mapping.status_code == 200
    assert mapping.json()["user_name"] == "Priya Nair"

    res = client.post(
        "/speakers/rooms/main/segments",
        headers={"Authorization": f"Bearer {alice}"},
        json={
            "session_id": "meeting-2",
            "source": "meeting_audio",
            "segments": [
                {
                    "speaker_label": "SPEAKER_00",
                    "text": "I changed the failing assertion",
                    "confidence": 0.87,
                },
                {
                    "speaker_label": "SPEAKER_01",
                    "text": "Can someone review it?",
                    "confidence": 0.66,
                },
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["segments"][0]["identity_source"] == "manual"

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {alice}"})
    messages = [
        message
        for message in snapshot.json()["messages"]
        if message["source"] == "meeting_audio"
    ]
    assert messages[0]["actor_name"] == "Priya Nair"
    assert messages[0]["metadata"]["identified_user_id"] == "user-priya"
    assert messages[1]["actor_name"] == "Unknown speaker"

    bob_join = _join(client, bob)
    handoff = bob_join.json()["handoff"]
    assert "Priya Nair [87%]: I changed the failing assertion" in handoff["lines"]
    assert "Unknown speaker (SPEAKER_01) [66%]: Can someone review it?" in handoff["lines"]


def test_rag_query_preserves_mapped_speaker_context(client):
    token = _token(client, "priya@voiceops.dev", "oncall123")
    _join(client, token)

    mapping = client.post(
        "/speakers/rooms/main/mappings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "speaker_label": "SPEAKER_00",
            "user_id": "user-priya",
            "confidence": 0.95,
            "source": "manual",
        },
    )
    assert mapping.status_code == 200

    ingest = client.post(
        "/speakers/rooms/main/segments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "meeting-rag-speaker",
            "source": "meeting_audio",
            "segments": [
                {
                    "speaker_label": "SPEAKER_00",
                    "text": "Priya mentioned speaker_context.py for the speaker memory audit",
                    "confidence": 0.91,
                }
            ],
        },
    )
    assert ingest.status_code == 200

    query = client.post(
        "/collab/rooms/main/rag/query",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "question": "what file did Priya mention for speaker memory?",
            "limit": 6,
            "include_code": False,
        },
    )

    assert query.status_code == 200
    data = query.json()
    assert "speaker_context.py" in data["answer"]
    speaker_citations = [
        citation
        for citation in data["citations"]
        if citation["actor_name"] == "Priya Nair"
        and citation["metadata"].get("speaker_label") == "SPEAKER_00"
    ]
    assert speaker_citations
    assert data["retrieval"]["provider"] == "local_sparse"


def test_speaker_validation_reports_unknown_mapping_work(client):
    token = _token(client, "priya@voiceops.dev", "oncall123")
    _join(client, token)
    ingest = client.post(
        "/speakers/rooms/main/segments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "validation-unknown",
            "source": "meeting_audio",
            "segments": [
                {"speaker_label": "SPEAKER_00", "text": "Need to validate this voice", "confidence": 0.66}
            ],
        },
    )
    assert ingest.status_code == 200

    report = client.get("/speakers/rooms/main/validation", headers={"Authorization": f"Bearer {token}"})

    assert report.status_code == 200
    data = report.json()
    assert data["status"] == "calibration_needed"
    assert data["ready"] is False
    assert data["unknown_speaker_labels"] == ["SPEAKER_00"]
    assert data["low_confidence_unknown_count"] == 1
    assert "Assign unknown speaker labels" in data["next_steps"][0]


def test_speaker_validation_ready_after_mapping_unknown_label(client):
    token = _token(client, "priya@voiceops.dev", "oncall123")
    _join(client, token)
    assert client.post(
        "/speakers/rooms/main/segments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "validation-mapped",
            "source": "meeting_audio",
            "segments": [
                {"speaker_label": "SPEAKER_00", "text": "Mapped speaker turn", "confidence": 0.86}
            ],
        },
    ).status_code == 200
    assert client.post(
        "/speakers/rooms/main/mappings",
        headers={"Authorization": f"Bearer {token}"},
        json={"speaker_label": "SPEAKER_00", "user_id": "user-priya", "confidence": 0.96, "source": "manual"},
    ).status_code == 200

    report = client.get("/speakers/rooms/main/validation", headers={"Authorization": f"Bearer {token}"})

    assert report.status_code == 200
    data = report.json()
    assert data["ready"] is True
    assert data["mapped_speaker_labels"] == ["SPEAKER_00"]
    assert data["unknown_speaker_labels"] == []


def test_speaker_validation_marks_single_speaker_whisperx_verification_as_weak(tmp_path):
    class WhisperishProvider(MockSpeakerProvider):
        name = "whisperx"

    verification_path = tmp_path / "speaker_verification.json"
    verification_path.write_text(
        '{"verified": true, "distinct_speaker_count": 1, "speaker_labels": ["SPEAKER_00"], "detail": "Verified one speaker"}',
        encoding="utf-8",
    )
    collab = CollaborationService(CollaborationStore(tmp_path / "collab.json"))
    service = SpeakerService(SpeakerStore(tmp_path / "speakers.json"), collab, WhisperishProvider())

    report = service.validation_report("main", Settings(speaker_verification_path=verification_path))

    assert report.ready is False
    assert report.verification_status == "weak"
    assert report.verification_count == 1
    assert "strict 2+ speaker verification" in " ".join(report.next_steps).lower()


def test_speaker_mapping_records_assignment_and_correction_audit(client):
    alice = _token(client, "priya@voiceops.dev", "oncall123")
    bob = _token(client, "admin@voiceops.dev", "admin123")
    _join(client, alice)
    _join(client, bob)

    first = client.post(
        "/speakers/rooms/main/mappings",
        headers={"Authorization": f"Bearer {alice}"},
        json={
            "speaker_label": "SPEAKER_00",
            "user_id": "user-priya",
            "confidence": 0.94,
            "source": "manual",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/speakers/rooms/main/mappings",
        headers={"Authorization": f"Bearer {bob}"},
        json={
            "speaker_label": "SPEAKER_00",
            "user_id": "user-admin",
            "confidence": 1,
            "source": "manual",
        },
    )
    assert second.status_code == 200

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {bob}"})
    messages = snapshot.json()["messages"]
    audit_text = [message["text"] for message in messages if message["source"] == "system"]

    assert "Priya Nair mapped SPEAKER_00 to Priya Nair." in audit_text
    assert "Sam Ortiz corrected SPEAKER_00 from Priya Nair to Sam Ortiz." in audit_text
    correction = next(message for message in messages if "corrected SPEAKER_00" in message["text"])
    assert correction["metadata"]["event"] == "speaker_mapping_corrected"
    assert correction["metadata"]["previous_user_name"] == "Priya Nair"

    handoff = client.get("/collab/rooms/main/handoff", headers={"Authorization": f"Bearer {alice}"})
    assert handoff.status_code == 200
    assert any(
        "Speaker identity: Sam Ortiz corrected SPEAKER_00 from Priya Nair to Sam Ortiz." == line
        for line in handoff.json()["lines"]
    )


def test_mapping_reattributes_existing_timeline_and_memory(client):
    alice = _token(client, "priya@voiceops.dev", "oncall123")
    _join(client, alice)

    res = client.post(
        "/speakers/rooms/main/segments",
        headers={"Authorization": f"Bearer {alice}"},
        json={
            "session_id": "meeting-3",
            "source": "meeting_audio",
            "segments": [
                {
                    "speaker_label": "SPEAKER_00",
                    "text": "Need to fix app.py health route",
                    "confidence": 0.68,
                },
            ],
        },
    )
    assert res.status_code == 200

    before = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {alice}"})
    before_message = before.json()["messages"][0]
    assert before_message["actor_name"] == "Unknown speaker"

    mapping = client.post(
        "/speakers/rooms/main/mappings",
        headers={"Authorization": f"Bearer {alice}"},
        json={
            "speaker_label": "SPEAKER_00",
            "user_id": "user-priya",
            "confidence": 0.97,
            "source": "manual",
        },
    )
    assert mapping.status_code == 200

    snapshot = client.get("/collab/rooms/main", headers={"Authorization": f"Bearer {alice}"})
    speaker_message = next(
        message
        for message in snapshot.json()["messages"]
        if message["source"] == "meeting_audio"
    )
    assert speaker_message["actor_name"] == "Priya Nair"
    assert speaker_message["metadata"]["identified_user_id"] == "user-priya"
    assert speaker_message["metadata"]["original_actor_name"] == "Unknown speaker"
    assert speaker_message["metadata"]["speaker_label"] == "SPEAKER_00"

    memory = client.get(
        "/collab/rooms/main/memory",
        headers={"Authorization": f"Bearer {alice}"},
        params={"q": "app.py", "kind": "task"},
    )
    assert memory.status_code == 200
    assert memory.json()[0]["actor_name"] == "Priya Nair"
    assert memory.json()[0]["metadata"]["original_actor_name"] == "Unknown speaker"

    audit = next(
        message
        for message in snapshot.json()["messages"]
        if message["metadata"].get("event") == "speaker_mapping_created"
    )
    assert audit["metadata"]["reattributed_messages"] == 1
    assert audit["metadata"]["reattributed_memory"] == 2
