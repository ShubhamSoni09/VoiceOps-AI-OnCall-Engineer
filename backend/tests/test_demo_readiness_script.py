from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "demo_readiness.py"
SPEC = importlib.util.spec_from_file_location("demo_readiness", SCRIPT_PATH)
demo_readiness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = demo_readiness
SPEC.loader.exec_module(demo_readiness)


def test_parse_json_stdout_handles_npm_prefix():
    output = """
> voiceops-frontend@0.1.0 e2e:all
> node tests/e2e/runAll.e2e.mjs --json

{"status":"ready","checks":[]}
"""

    assert demo_readiness.parse_json_stdout(output) == {"status": "ready", "checks": []}


def test_demo_readiness_allows_optional_real_diarization(monkeypatch):
    monkeypatch.setattr(demo_readiness, "run_command_gate", _passing_command_gate)
    monkeypatch.setattr(
        demo_readiness,
        "_speaker_verification_status",
        lambda _settings: _verification(False, last_stage="diarizing", warnings=["CPU mode may lag"]),
    )

    report = demo_readiness.run_demo_readiness(timeout=1)

    assert report["ready"] is True
    real_gate = _gate(report, "real_diarization_status")
    assert real_gate["status"] == "skipped"
    assert real_gate["required"] is False
    assert "Last real diarization stage: diarizing." in report["next_steps"]
    assert "CPU mode may lag" in report["next_steps"]
    assert any("WHISPERX_WORKER_MODE=persistent_subprocess" in step for step in report["next_steps"])
    assert any("--real-audio" in step for step in report["next_steps"])
    assert any("--require-real-live-meeting" in step for step in report["next_steps"])


def test_demo_readiness_can_require_recorded_real_diarization(monkeypatch):
    monkeypatch.setattr(demo_readiness, "run_command_gate", _passing_command_gate)
    monkeypatch.setattr(demo_readiness, "_speaker_verification_status", lambda _settings: _verification(False))

    report = demo_readiness.run_demo_readiness(require_real_diarization=True, timeout=1)

    assert report["ready"] is False
    assert report["required_failed"] == ["real_diarization_status"]
    real_gate = _gate(report, "real_diarization_status")
    assert real_gate["status"] == "failed"
    assert real_gate["required"] is True


def test_demo_readiness_real_audio_runs_recorded_whisperx_smoke(monkeypatch, tmp_path):
    commands = []

    def fake_command_gate(**kwargs):
        commands.append(kwargs["command"])
        return _passing_command_gate(**kwargs)

    monkeypatch.setattr(demo_readiness, "run_command_gate", fake_command_gate)
    monkeypatch.setattr(demo_readiness, "_speaker_verification_status", lambda _settings: _verification(True))

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")
    report = demo_readiness.run_demo_readiness(
        real_audio=audio,
        require_real_diarization=True,
        timeout=1,
        real_in_process=True,
        real_diarization_load_timeout=42,
    )

    assert report["ready"] is True
    smoke_command = commands[0]
    assert "scripts/smoke_whisperx_provider.py" in smoke_command
    assert "--record-status" in smoke_command
    assert "--require-multiple-speakers" in smoke_command
    assert "--in-process" in smoke_command
    assert "--max-audio-seconds" in smoke_command
    assert smoke_command[smoke_command.index("--max-audio-seconds") + 1] == "12.0"
    assert "--diarization-load-timeout" in smoke_command
    assert smoke_command[smoke_command.index("--diarization-load-timeout") + 1] == "42"
    assert "--model" in smoke_command
    assert smoke_command[smoke_command.index("--model") + 1] == "tiny"
    assert str(audio) in smoke_command


def test_demo_readiness_can_require_real_live_meeting_smoke(monkeypatch, tmp_path):
    commands = []

    def fake_command_gate(**kwargs):
        commands.append(kwargs)
        return _passing_command_gate(**kwargs)

    monkeypatch.setattr(demo_readiness, "run_command_gate", fake_command_gate)
    monkeypatch.setattr(demo_readiness, "_speaker_verification_status", lambda _settings: _verification(True))

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")
    report = demo_readiness.run_demo_readiness(
        real_audio=audio,
        require_real_diarization=True,
        require_real_live_meeting=True,
        timeout=1,
        real_model="tiny",
        real_live_worker_mode="persistent_subprocess",
        real_live_chunks=2,
    )

    assert report["ready"] is True
    live_gate = _gate(report, "real_live_meeting_smoke")
    assert live_gate["status"] == "passed"
    assert live_gate["required"] is True
    live_command = next(
        item["command"] for item in commands if item["gate_id"] == "real_live_meeting_smoke"
    )
    assert "scripts/smoke_live_meeting_real.py" in live_command
    assert "--receive-timeout" in live_command
    assert "--max-audio-seconds" in live_command
    assert "--model" in live_command
    assert "--worker-mode" in live_command
    assert live_command[live_command.index("--worker-mode") + 1] == "persistent_subprocess"
    assert "--chunks" in live_command
    assert live_command[live_command.index("--chunks") + 1] == "2"
    assert "--in-process-warmup" not in live_command
    assert str(audio) in live_command


def _passing_command_gate(**kwargs):
    return demo_readiness.GateResult(
        id=kwargs["gate_id"],
        label=kwargs["label"],
        status="passed",
        required=kwargs["required"],
        detail="passed",
        command=list(kwargs["command"]),
        cwd=str(kwargs["cwd"]),
        exit_code=0,
    )


def _verification(verified: bool, *, last_stage: str | None = None, warnings: list[str] | None = None):
    return SimpleNamespace(
        verified=verified,
        distinct_speaker_count=2 if verified else 0,
        detail="Verified 2 speaker label(s)" if verified else "No real diarization smoke report has been recorded",
        last_stage=last_stage,
        warnings=warnings or [],
        model_dump=lambda mode="json": {
            "verified": verified,
            "distinct_speaker_count": 2 if verified else 0,
            "speaker_labels": ["SPEAKER_00", "SPEAKER_01"] if verified else [],
            "last_stage": last_stage,
            "warnings": warnings or [],
        },
    )


def _gate(report: dict, gate_id: str) -> dict:
    return next(gate for gate in report["gates"] if gate["id"] == gate_id)
