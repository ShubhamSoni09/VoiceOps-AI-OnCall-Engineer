from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "demo_operator.py"
SPEC = importlib.util.spec_from_file_location("demo_operator", SCRIPT_PATH)
demo_operator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = demo_operator
SPEC.loader.exec_module(demo_operator)


def test_operator_quick_plan_prints_non_executing_commands():
    report = demo_operator.plan_report("quick", timeout=123)

    assert report["status"] == "plan"
    assert [command["id"] for command in report["commands"]] == [
        "target_readiness",
        "demo_readiness_fast",
    ]
    assert report["commands"][1]["required"] is False
    assert all("shell_hint" in command for command in report["commands"])
    assert "Run again with --run" in report["next_steps"][0]


def test_operator_local_profile_sets_mock_provider():
    commands = demo_operator.command_plan("local", timeout=222)

    assert [command.id for command in commands] == ["demo_readiness_full", "target_readiness"]
    assert commands[0].env["SPEAKER_PROVIDER"] == "mock"
    assert "--timeout" in commands[0].command
    assert commands[0].command[commands[0].command.index("--timeout") + 1] == "222"


def test_operator_real_mac_profile_sets_whisperx_provider():
    commands = demo_operator.command_plan("real-mac", timeout=333)

    assert [command.id for command in commands] == ["real_demo_readiness", "target_readiness_required"]
    assert commands[0].env["SPEAKER_PROVIDER"] == "whisperx"
    assert commands[0].env["WHISPERX_MODEL"] == "tiny"
    assert "--generate-macos-tts" in commands[0].command
    assert "--require-real-live-meeting" in commands[0].command
    assert "--require-ready" in commands[1].command


def test_operator_production_trial_profile_runs_sqlite_closure():
    commands = demo_operator.command_plan("production-trial", timeout=222)

    assert [command.id for command in commands] == ["sqlite_meeting_closure", "operator_acceptance"]
    assert "--collab-backend" in commands[0].command
    assert commands[0].command[commands[0].command.index("--collab-backend") + 1] == "sqlite"
    assert "scripts/smoke_meeting_closure.py" in commands[0].command
    assert "--require-accepted" in commands[1].command
    assert commands[0].env["LLM_PROVIDER"] == "mock"


def test_operator_real_mac_preflight_blocks_missing_requirements(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(demo_operator.shutil, "which", lambda _name: None)

    report = demo_operator.run_profile("real-mac", timeout=1, keep_going=False)

    assert report["ready"] is False
    assert report["results"] == []
    assert report["skipped_after_failure"] == ["real_demo_readiness", "target_readiness_required"]
    assert {item["id"] for item in report["preflight"]} == {
        "env_hf_token",
        "binary_ffmpeg",
        "binary_say",
    }
    assert any("HF_TOKEN" in step for step in report["next_steps"])


def test_operator_real_mac_preflight_allows_run_when_ready(monkeypatch):
    calls = []
    monkeypatch.setenv("HF_TOKEN", "hf_fake")
    monkeypatch.setattr(demo_operator.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"status":"ready"}', stderr="")

    monkeypatch.setattr(demo_operator.subprocess, "run", fake_run)

    report = demo_operator.run_profile("real-mac", timeout=1, keep_going=False)

    assert report["ready"] is True
    assert all(item["status"] == "passed" for item in report["preflight"])
    assert [result["status"] for result in report["results"]] == ["passed", "passed"]
    assert len(calls) == 2


def test_operator_run_profile_collects_success(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"status":"ready"}', stderr="")

    monkeypatch.setattr(demo_operator.subprocess, "run", fake_run)

    report = demo_operator.run_profile("quick", timeout=1, keep_going=False)

    assert report["ready"] is True
    assert [result["status"] for result in report["results"]] == ["passed", "passed"]
    assert len(calls) == 2
    assert calls[0][1]["env"]["LLM_PROVIDER"] == "mock"


def test_operator_run_profile_stops_after_required_failure(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=2, stdout='{"status":"needs_attention","next_steps":["fix gate"]}', stderr="")

    monkeypatch.setattr(demo_operator.subprocess, "run", fake_run)

    report = demo_operator.run_profile("local", timeout=1, keep_going=False)

    assert report["ready"] is False
    assert [result["id"] for result in report["results"]] == ["demo_readiness_full"]
    assert report["skipped_after_failure"] == ["target_readiness"]
    assert "fix gate" in report["next_steps"][0]
    assert len(calls) == 1


def test_operator_optional_nonzero_command_is_diagnostic(monkeypatch):
    responses = [
        SimpleNamespace(returncode=0, stdout='{"status":"ready"}', stderr=""),
        SimpleNamespace(returncode=2, stdout='{"status":"needs_attention","required_skipped":["backend_tests"]}', stderr=""),
    ]

    def fake_run(command, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(demo_operator.subprocess, "run", fake_run)

    report = demo_operator.run_profile("quick", timeout=1, keep_going=False)

    assert report["ready"] is True
    assert report["results"][1]["status"] == "diagnostic"
    assert report["results"][1]["detail"] == "needs_attention"
