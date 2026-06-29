from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.speakers.models import LiveProviderResult, SpeakerSegment
from app.speakers.provider import MockSpeakerProvider


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "smoke_live_meeting_real.py"
SPEC = importlib.util.spec_from_file_location("smoke_live_meeting_real", SCRIPT_PATH)
smoke_live_meeting_real = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = smoke_live_meeting_real
SPEC.loader.exec_module(smoke_live_meeting_real)


def test_real_live_smoke_in_process_warmup_reuses_provider_before_live_chunk(monkeypatch, tmp_path):
    calls = []
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake audio")

    class FakeProvider(MockSpeakerProvider):
        name = "whisperx"

        async def warmup(self, stage_callback=None):
            calls.append("warmup")
            if stage_callback:
                stage_callback("completed", "fake warmup completed")

        async def process_live_chunk(self, *_args, stage_callback=None, **_kwargs):
            calls.append("process")
            sequence = int(_kwargs.get("sequence") or len(calls))
            if stage_callback:
                stage_callback("transcribing", "fake transcribe")
                stage_callback("diarizing", "fake diarize")
            return LiveProviderResult(
                temp_id=f"real-live-smoke-{sequence}",
                partial_text="Alice speaks. Bob answers.",
                start_ms=0,
                end_ms=2000,
                segments=[
                    SpeakerSegment(speaker_label="SPEAKER_00", text="Alice speaks.", start_ms=0, end_ms=1000),
                    SpeakerSegment(speaker_label="SPEAKER_01", text="Bob answers.", start_ms=1000, end_ms=2000),
                ],
            )

    monkeypatch.setattr(smoke_live_meeting_real, "ensure_pyannote_assets", lambda _settings, _report: None)
    monkeypatch.setattr(smoke_live_meeting_real, "prepare_audio_sample", lambda path, *_args: path)
    monkeypatch.setattr(smoke_live_meeting_real, "mime_type_for_path", lambda _path: "audio/wav")
    monkeypatch.setattr(smoke_live_meeting_real, "get_speaker_provider", lambda *_args, **_kwargs: FakeProvider())

    exit_code, report = smoke_live_meeting_real.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=1,
        receive_timeout=5,
        model="tiny",
        device="cpu",
        compute_type="int8",
        worker_mode="subprocess",
        max_audio_seconds=12,
        chunks=2,
        num_speakers=None,
        min_speakers=2,
        max_speakers=2,
        in_process_warmup=True,
        allow_unsafe_in_process_warmup=False,
        warmup_timeout=1,
        keep_audio=False,
    )

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["warmup_enabled"] is True
    assert report["warmup_elapsed_ms"] >= 0
    assert report["live_elapsed_ms"] >= 0
    assert calls == ["warmup", "process", "process"]
    assert report["distinct_speaker_count"] == 2
    assert report["timeline_message_count"] == 4
    assert report["completed_chunks"] == 2
    assert len(report["chunk_results"]) == 2


def test_real_live_smoke_refuses_unsafe_real_in_process_warmup(monkeypatch, tmp_path):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake audio")

    class RealLikeProvider(MockSpeakerProvider):
        name = "whisperx"

        def _warmup_sync_locked(self, **_kwargs):
            raise AssertionError("unsafe warmup should not run without an explicit override")

    monkeypatch.setattr(smoke_live_meeting_real, "ensure_pyannote_assets", lambda _settings, _report: None)
    monkeypatch.setattr(smoke_live_meeting_real, "prepare_audio_sample", lambda path, *_args: path)
    monkeypatch.setattr(smoke_live_meeting_real, "get_speaker_provider", lambda *_args, **_kwargs: RealLikeProvider())

    exit_code, report = smoke_live_meeting_real.run_smoke(
        audio_path=audio,
        generate_macos_tts=False,
        alice_text="Alice",
        bob_text="Bob",
        timeout=1,
        receive_timeout=5,
        model="tiny",
        device="cpu",
        compute_type="int8",
        worker_mode="subprocess",
        max_audio_seconds=12,
        chunks=1,
        num_speakers=None,
        min_speakers=2,
        max_speakers=2,
        in_process_warmup=True,
        allow_unsafe_in_process_warmup=False,
        warmup_timeout=1,
        keep_audio=False,
    )

    assert exit_code == 7
    assert "disabled by default" in report["error"]
    assert report["elapsed_ms"] >= 0
