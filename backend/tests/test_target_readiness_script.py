from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from app.system.router import TargetReadinessMilestone, TargetReadinessResponse


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "target_readiness.py"
SPEC = importlib.util.spec_from_file_location("target_readiness", SCRIPT_PATH)
target_readiness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = target_readiness
SPEC.loader.exec_module(target_readiness)


def test_target_readiness_script_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(target_readiness, "run_target_readiness", lambda: _report(ready=True))

    exit_code = target_readiness.main(["--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ready"] is True
    assert output["score"] == 100
    assert output["milestones"][0]["id"] == "real_browser_live"


def test_target_readiness_script_require_ready_fails_when_blocked(monkeypatch, capsys):
    monkeypatch.setattr(target_readiness, "run_target_readiness", lambda: _report(ready=False))

    exit_code = target_readiness.main(["--require-ready"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "VoiceOps target readiness: needs_attention" in output
    assert "Run the real browser microphone live gate." in output


def test_target_readiness_script_default_is_diagnostic(monkeypatch):
    monkeypatch.setattr(target_readiness, "run_target_readiness", lambda: _report(ready=False))

    assert target_readiness.main([]) == 0


def test_target_readiness_script_loads_env_file(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    env_path = tmp_path / ".env.production"
    env_path.write_text(f"VOICEOPS_WORKSPACE={workspace}\n", encoding="utf-8")
    captured = {}

    def fake_target(settings):
        captured["workspace"] = settings.voiceops_workspace
        return _report(ready=True)

    monkeypatch.setattr(target_readiness, "_target_readiness", fake_target)

    exit_code = target_readiness.main(["--env-file", str(env_path), "--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ready"] is True
    assert captured["workspace"] == str(workspace)


def _report(*, ready: bool) -> TargetReadinessResponse:
    return TargetReadinessResponse(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checked_at="2026-06-18T12:00:00+00:00",
        score=100 if ready else 83,
        ready_count=1 if ready else 0,
        total_count=1,
        milestones=[
            TargetReadinessMilestone(
                id="real_browser_live",
                label="Browser mic to speaker labels",
                ready=ready,
                status="ready" if ready else "unproven",
                detail="Browser live evidence is present" if ready else "Browser live evidence is missing",
                evidence="passed | 2 speakers" if ready else None,
                next_action=None if ready else "Run the real browser microphone live gate.",
                command="cd frontend && npm run e2e:all -- --json --real-browser-live",
            )
        ],
        next_steps=[] if ready else ["Run the real browser microphone live gate."],
    )
