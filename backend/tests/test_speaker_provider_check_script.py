from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.config import Settings
from app.speakers.models import LiveProviderResult, SpeakerSegment


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "check_speaker_provider.py"
SPEC = importlib.util.spec_from_file_location("check_speaker_provider", SCRIPT_PATH)
check_speaker_provider = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_speaker_provider)

SMOKE_SCRIPT_PATH = SCRIPTS_ROOT / "smoke_whisperx_provider.py"
SMOKE_SPEC = importlib.util.spec_from_file_location("smoke_whisperx_provider", SMOKE_SCRIPT_PATH)
smoke_whisperx_provider = importlib.util.module_from_spec(SMOKE_SPEC)
assert SMOKE_SPEC.loader is not None
SMOKE_SPEC.loader.exec_module(smoke_whisperx_provider)
ORIGINAL_ENSURE_PYANNOTE_ASSETS = smoke_whisperx_provider.ensure_pyannote_assets


@pytest.fixture(autouse=True)
def disable_real_pyannote_asset_download(monkeypatch):
    monkeypatch.setattr(smoke_whisperx_provider, "ensure_pyannote_assets", lambda _settings, _report: None)


def test_check_speaker_provider_reports_mock_ready():
    report = check_speaker_provider.build_report(Settings(speaker_provider="mock"))

    assert report["provider"] == "mock"
    assert report["ready"] is True
    assert any("SPEAKER_PROVIDER=whisperx" in step for step in report["next_steps"])


def test_check_speaker_provider_reports_missing_hf_token():
    report = check_speaker_provider.build_report(
        Settings(
            speaker_provider="whisperx",
            hf_token=None,
            whisperx_device="cpu",
            whisperx_compute_type="int8",
        )
    )

    hf_token = next(check for check in report["checks"] if check["id"] == "hf_token")
    assert hf_token["ready"] is False
    assert report["ready"] is False
    assert any("HF_TOKEN" in step for step in report["next_steps"])


def test_check_speaker_provider_recommends_recorded_smoke_after_ready(monkeypatch):
    monkeypatch.setattr(check_speaker_provider, "_speaker_provider_status", lambda _settings: _ready_status())

    report = check_speaker_provider.build_report(
        Settings(speaker_provider="whisperx", hf_token="hf_test_token")
    )

    assert report["ready"] is True
    assert report["config"]["worker_mode"] == "subprocess"
    assert any("--record-status" in step for step in report["next_steps"])
    assert any("WHISPERX_WORKER_MODE=persistent_subprocess" in step for step in report["next_steps"])


def test_whisperx_smoke_maps_common_audio_mime_types():
    assert smoke_whisperx_provider.mime_type_for_path(Path("sample.wav")) == "audio/wav"
    assert smoke_whisperx_provider.mime_type_for_path(Path("sample.mp3")) == "audio/mpeg"
    assert smoke_whisperx_provider.mime_type_for_path(Path("sample.webm")) == "audio/webm"
    assert smoke_whisperx_provider.mime_type_for_path(Path("sample.bin")) == "application/octet-stream"


def test_whisperx_smoke_prepares_trimmed_audio_sample(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    trimmed = tmp_path / "meeting-smoke-trimmed.wav"
    audio.write_bytes(b"full")
    commands = []

    monkeypatch.setattr(smoke_whisperx_provider, "audio_duration_seconds", lambda path: 30.0 if path == audio else 12.0)
    monkeypatch.setattr(smoke_whisperx_provider.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    def fake_run_checked(command):
        commands.append(command)
        trimmed.write_bytes(b"trimmed")

    monkeypatch.setattr(smoke_whisperx_provider, "run_checked", fake_run_checked)
    report = {}

    result = smoke_whisperx_provider.prepare_audio_sample(audio, tmp_path, 12.0, report)

    assert result == trimmed
    assert report["audio_trimmed"] is True
    assert report["audio_seconds"] == 12.0
    assert report["audio_path"] == str(trimmed)
    assert "-t" in commands[0]
    assert commands[0][commands[0].index("-t") + 1] == "12.0"


def test_whisperx_smoke_preloads_required_pyannote_assets(monkeypatch):
    monkeypatch.setattr(smoke_whisperx_provider, "ensure_pyannote_assets", ORIGINAL_ENSURE_PYANNOTE_ASSETS)
    downloaded = []

    def fake_download(model_id, filename, **kwargs):
        downloaded.append((model_id, filename, kwargs))
        return f"/cache/{filename}"

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(hf_hub_download=fake_download),
    )
    report = {"stages": [], "config": {}}

    smoke_whisperx_provider.ensure_pyannote_assets(
        Settings(speaker_provider="whisperx", hf_token="hf_test_token"),
        report,
    )

    assert [item[1] for item in downloaded] == list(smoke_whisperx_provider.PYANNOTE_REQUIRED_ASSETS)
    assert all(item[0] == smoke_whisperx_provider.PYANNOTE_MODEL_ID for item in downloaded)
    assert all(item[2]["token"] == "hf_test_token" for item in downloaded)
    assert report["stages"][-1]["stage"] == "pyannote_assets_ready"
    assert report["config"]["pyannote_required_assets"] == list(smoke_whisperx_provider.PYANNOTE_REQUIRED_ASSETS)


def test_whisperx_smoke_reports_pyannote_asset_preload_failure(monkeypatch):
    monkeypatch.setattr(smoke_whisperx_provider, "ensure_pyannote_assets", ORIGINAL_ENSURE_PYANNOTE_ASSETS)
    def fake_download(_model_id, filename, **_kwargs):
        if filename == "embedding/pytorch_model.bin":
            raise RuntimeError("denied")
        return f"/cache/{filename}"

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(hf_hub_download=fake_download),
    )
    report = {"stages": [], "config": {}}

    with pytest.raises(RuntimeError, match="pyannote model asset preload failed"):
        smoke_whisperx_provider.ensure_pyannote_assets(
            Settings(speaker_provider="whisperx", hf_token="hf_test_token"),
            report,
        )

    assert report["stages"][-1]["stage"] == "pyannote_assets_failed"


@pytest.mark.asyncio
async def test_whisperx_smoke_uses_provider_and_reports_segments(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake-audio")
    monkeypatch.setattr(smoke_whisperx_provider, "_speaker_provider_status", lambda _settings: _ready_status())
    monkeypatch.setattr(smoke_whisperx_provider, "WhisperXSpeakerProvider", FakeWhisperXProvider)
    monkeypatch.setattr(smoke_whisperx_provider, "audio_duration_seconds", lambda _path: 2.0)

    exit_code, report = await smoke_whisperx_provider.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=1,
        keep_audio=False,
        require_multiple_speakers=True,
        in_process=True,
        model="tiny",
        device="cpu",
        compute_type="int8",
        max_audio_seconds=12,
    )

    assert exit_code == 0
    assert report["execution_mode"] == "in_process"
    assert any("manual diagnostics" in warning for warning in report["warnings"])
    assert report["speaker_labels"] == ["SPEAKER_00", "SPEAKER_01"]
    assert report["distinct_speaker_count"] == 2
    assert report["partial_text"] == "Alice. Bob."
    assert report["stages"][0]["stage"] == "transcribing"
    assert report["config"]["whisperx_model"] == "tiny"
    assert report["config"]["whisperx_device"] == "cpu"
    assert report["config"]["whisperx_compute_type"] == "int8"
    assert report["config"]["max_audio_seconds"] == 12
    assert report["config"]["whisperx_min_speakers"] == 2
    assert report["config"]["whisperx_max_speakers"] == 2


def test_whisperx_smoke_defaults_strict_multi_speaker_hint():
    hints = smoke_whisperx_provider.default_speaker_hints(
        require_multiple_speakers=True,
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
    )

    assert hints == {"whisperx_min_speakers": 2, "whisperx_max_speakers": 2}


def test_whisperx_smoke_exact_speaker_hint_wins():
    hints = smoke_whisperx_provider.default_speaker_hints(
        require_multiple_speakers=True,
        num_speakers=2,
        min_speakers=1,
        max_speakers=4,
    )

    assert hints == {"whisperx_num_speakers": 2}


def test_whisperx_smoke_writes_sanitized_verification_status(tmp_path):
    path = tmp_path / "speaker_verification.json"
    report = {
        "provider": "whisperx",
        "generated_audio": True,
        "audio_path": "/tmp/private.wav",
        "partial_text": "Alice private transcript",
        "segments": [{"speaker_label": "SPEAKER_00", "text": "private"}],
        "speaker_labels": ["SPEAKER_00"],
        "distinct_speaker_count": 1,
        "elapsed_ms": 55,
        "audio_seconds": 12.25,
        "execution_mode": "subprocess",
        "warnings": ["CPU diarization can exceed the smoke timeout."],
        "config": {
            "whisperx_model": "tiny",
            "whisperx_device": "cpu",
            "whisperx_compute_type": "int8",
            "worker_mode": "in_process",
            "whisperx_min_speakers": 2,
            "whisperx_max_speakers": 2,
            "diarization_load_timeout_seconds": 30,
            "hf_token": "secret",
        },
        "stages": [
            {"stage": "loading_model", "message": "Loading WhisperX tiny", "elapsed_ms": 10},
            {"stage": "diarizing", "message": "Loading pyannote diarization pipeline", "elapsed_ms": 20},
        ],
    }

    status = smoke_whisperx_provider.build_verification_status(
        report,
        exit_code=0,
        require_multiple_speakers=False,
    )
    smoke_whisperx_provider.write_verification_status(path, status)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["verified"] is True
    assert saved["speaker_labels"] == ["SPEAKER_00"]
    assert saved["generated_audio"] is True
    assert "partial_text" not in saved
    assert "audio_path" not in saved
    assert "segments" not in saved
    assert saved["last_stage"] == "diarizing"
    assert saved["warnings"] == ["CPU diarization can exceed the smoke timeout."]
    assert saved["quality"] == {
        "level": "verified",
        "audio_seconds": 12.25,
        "segment_count": 1,
        "has_transcript": True,
        "multi_speaker": False,
        "strict_passed": True,
        "notes": [],
    }
    assert saved["config"]["whisperx_model"] == "tiny"
    assert saved["config"]["worker_mode"] == "in_process"
    assert saved["config"]["whisperx_min_speakers"] == 2
    assert saved["config"]["whisperx_max_speakers"] == 2
    assert saved["config"]["diarization_load_timeout_seconds"] == 30
    assert "hf_token" not in saved["config"]


def test_whisperx_smoke_quality_flags_weak_short_strict_sample():
    status = smoke_whisperx_provider.build_verification_status(
        {
            "audio_seconds": 1.8,
            "partial_text": "Alice only",
            "segments": [{"speaker_label": "SPEAKER_00", "text": "Alice"}],
            "speaker_labels": ["SPEAKER_00"],
        },
        exit_code=0,
        require_multiple_speakers=True,
    )

    assert status["verified"] is True
    assert status["quality"]["level"] == "weak"
    assert status["quality"]["strict_passed"] is False
    assert status["quality"]["multi_speaker"] is False
    assert any("very short" in note for note in status["quality"]["notes"])
    assert any("Strict multi-speaker" in note for note in status["quality"]["notes"])


def test_whisperx_smoke_records_failure_verification_status():
    status = smoke_whisperx_provider.build_verification_status(
        {"error": "timeout", "speaker_labels": [], "segments": []},
        exit_code=5,
        require_multiple_speakers=True,
    )

    assert status["verified"] is False
    assert status["strict_multi_speaker"] is True
    assert status["error"] == "timeout"


def test_whisperx_smoke_compacts_multiline_errors():
    error = "WhisperX worker timed out: stdout:\nstage detail\nstderr:\nwarning"

    assert smoke_whisperx_provider.compact_error(error) == "WhisperX worker timed out"


@pytest.mark.asyncio
async def test_whisperx_smoke_can_require_multiple_speakers(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake-audio")
    monkeypatch.setattr(smoke_whisperx_provider, "_speaker_provider_status", lambda _settings: _ready_status())
    monkeypatch.setattr(smoke_whisperx_provider, "WhisperXSpeakerProvider", SingleSpeakerProvider)

    exit_code, report = await smoke_whisperx_provider.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=1,
        keep_audio=False,
        require_multiple_speakers=True,
        in_process=False,
    )

    assert exit_code == 4
    assert report["execution_mode"] == "subprocess"
    assert "Expected at least two speaker labels" in report["error"]


@pytest.mark.asyncio
async def test_whisperx_smoke_timeout_reports_elapsed_and_hint(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake-audio")
    monkeypatch.setattr(smoke_whisperx_provider, "_speaker_provider_status", lambda _settings: _ready_status())
    monkeypatch.setattr(smoke_whisperx_provider, "WhisperXSpeakerProvider", TimeoutProvider)

    exit_code, report = await smoke_whisperx_provider.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=1,
        keep_audio=False,
        require_multiple_speakers=True,
        in_process=False,
        model="tiny",
        device="cpu",
        compute_type="int8",
    )

    assert exit_code == 1
    assert report["elapsed_ms"] >= 0
    assert "timed out" in report["error"]
    assert any("CPU diarization" in warning for warning in report["warnings"])


@pytest.mark.asyncio
async def test_whisperx_smoke_fails_fast_when_pyannote_load_stalls(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake-audio")
    monkeypatch.setattr(smoke_whisperx_provider, "_speaker_provider_status", lambda _settings: _ready_status())
    monkeypatch.setattr(smoke_whisperx_provider, "WhisperXSpeakerProvider", HangingDiarizationProvider)

    exit_code, report = await smoke_whisperx_provider.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=30,
        keep_audio=False,
        require_multiple_speakers=True,
        in_process=False,
        model="tiny",
        device="cpu",
        compute_type="int8",
        diarization_load_timeout=0.01,
    )

    assert exit_code == 6
    assert "pyannote diarization model load timed out" in report["error"]
    assert report["stages"][-1]["stage"] == "diarizing"
    assert any("pyannote model loading" in warning for warning in report["warnings"])


@pytest.mark.asyncio
async def test_whisperx_smoke_in_process_timeout_is_bounded(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake-audio")
    monkeypatch.setattr(smoke_whisperx_provider, "_speaker_provider_status", lambda _settings: _ready_status())
    monkeypatch.setattr(smoke_whisperx_provider, "WhisperXSpeakerProvider", SlowSyncProvider)
    monkeypatch.setattr(smoke_whisperx_provider, "in_process_deadline", immediate_timeout)

    exit_code, report = await smoke_whisperx_provider.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=0.01,
        keep_audio=False,
        require_multiple_speakers=True,
        in_process=True,
        model="tiny",
        device="cpu",
        compute_type="int8",
    )

    assert exit_code == 5
    assert report["execution_mode"] == "in_process"
    assert report["elapsed_ms"] >= 0
    assert "timed out" in report["error"]


def _ready_status():
    return SimpleNamespace(
        value="whisperx",
        ready=True,
        detail="ready",
        checks=[
            SimpleNamespace(model_dump=lambda: {"id": "hf_token", "label": "HF_TOKEN", "ready": True}),
        ],
    )


class FakeWhisperXProvider:
    def __init__(self, _settings, **_kwargs):
        pass

    async def process_live_chunk(self, *_args, stage_callback=None, **_kwargs):
        if stage_callback:
            stage_callback("transcribing", "fake transcribe")
        return LiveProviderResult(
            temp_id="manual-whisperx-smoke-1",
            partial_text="Alice. Bob.",
            segments=[
                SpeakerSegment(speaker_label="SPEAKER_00", text="Alice.", start_ms=0, end_ms=900),
                SpeakerSegment(speaker_label="SPEAKER_01", text="Bob.", start_ms=1000, end_ms=1800),
            ],
        )


class SingleSpeakerProvider(FakeWhisperXProvider):
    async def process_live_chunk(self, *_args, stage_callback=None, **_kwargs):
        return LiveProviderResult(
            temp_id="manual-whisperx-smoke-1",
            partial_text="Alice only.",
            segments=[SpeakerSegment(speaker_label="SPEAKER_00", text="Alice only.")],
        )


class TimeoutProvider(FakeWhisperXProvider):
    async def process_live_chunk(self, *_args, **_kwargs):
        raise RuntimeError("WhisperX worker timed out")


class HangingDiarizationProvider(FakeWhisperXProvider):
    async def process_live_chunk(self, *_args, stage_callback=None, **_kwargs):
        if stage_callback:
            stage_callback("diarizing", "Loading pyannote diarization pipeline")
        await asyncio.sleep(60)
        return LiveProviderResult(temp_id="too-late", segments=[])


class SlowSyncProvider(FakeWhisperXProvider):
    def _process_live_chunk_sync(self, *_args, **_kwargs):
        return LiveProviderResult(temp_id="too-late", segments=[])


@contextmanager
def immediate_timeout(_seconds):
    raise TimeoutError("In-process WhisperX smoke timed out after 1s.")
    yield
