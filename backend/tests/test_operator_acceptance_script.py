from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "operator_acceptance.py"
SPEC = importlib.util.spec_from_file_location("operator_acceptance", SCRIPT_PATH)
operator_acceptance = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = operator_acceptance
SPEC.loader.exec_module(operator_acceptance)


def test_operator_acceptance_aggregates_ready_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(operator_acceptance, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(operator_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(operator_acceptance, "build_event_store_readiness", lambda _settings: _event_store(True))
    monkeypatch.setattr(operator_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = operator_acceptance.run_operator_acceptance(project_root=tmp_path)

    assert report["accepted"] is True
    assert report["status"] == "accepted"
    assert [stage["id"] for stage in report["stages"]] == [
        "team_onboarding",
        "production_security",
        "event_store_readiness",
        "final_acceptance",
    ]
    assert report["stages"][1]["required"] is False
    assert report["stages"][2]["required"] is False


def test_operator_acceptance_requires_security_when_env_file_is_supplied(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DEPLOYMENT_ENVIRONMENT=production\n", encoding="utf-8")
    monkeypatch.setattr(operator_acceptance, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(operator_acceptance, "run_security_readiness", lambda **_kwargs: _security(False))
    monkeypatch.setattr(operator_acceptance, "build_event_store_readiness", lambda _settings: _event_store(True))
    monkeypatch.setattr(operator_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = operator_acceptance.run_operator_acceptance(env_file=env_path, project_root=tmp_path)
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["production_security"]["required"] is True
    assert stages["production_security"]["ready"] is False
    assert "rotate secret" in report["next_steps"]


def test_operator_acceptance_treats_local_env_security_as_diagnostic(monkeypatch, tmp_path):
    env_path = tmp_path / ".env.local"
    env_path.write_text("DEPLOYMENT_ENVIRONMENT=local\nCOLLAB_STORE_BACKEND=sqlite\n", encoding="utf-8")
    monkeypatch.setattr(operator_acceptance, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(operator_acceptance, "run_security_readiness", lambda **_kwargs: _security(False))
    monkeypatch.setattr(operator_acceptance, "build_event_store_readiness", lambda _settings: _event_store(True))
    monkeypatch.setattr(operator_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = operator_acceptance.run_operator_acceptance(env_file=env_path, project_root=tmp_path)
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is True
    assert stages["production_security"]["required"] is False
    assert stages["production_security"]["ready"] is True
    assert stages["production_security"]["status"] == "diagnostic"
    assert stages["event_store_readiness"]["required"] is True
    assert "rotate secret" in report["next_steps"]


def test_operator_acceptance_requires_event_store_when_env_file_is_supplied(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DEPLOYMENT_ENVIRONMENT=production\n", encoding="utf-8")
    monkeypatch.setattr(operator_acceptance, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(operator_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(operator_acceptance, "build_event_store_readiness", lambda _settings: _event_store(False))
    monkeypatch.setattr(operator_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = operator_acceptance.run_operator_acceptance(env_file=env_path, project_root=tmp_path)
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["event_store_readiness"]["required"] is True
    assert stages["event_store_readiness"]["ready"] is False
    assert "run migration" in report["next_steps"]


def test_operator_acceptance_profile_failure_blocks_acceptance(monkeypatch, tmp_path):
    monkeypatch.setattr(operator_acceptance, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(operator_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(operator_acceptance, "build_event_store_readiness", lambda _settings: _event_store(True))
    monkeypatch.setattr(operator_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))
    monkeypatch.setattr(operator_acceptance, "run_profile", lambda *_args, **_kwargs: _profile(False))

    report = operator_acceptance.run_operator_acceptance(
        run_profile_name="quick",
        project_root=tmp_path,
    )

    assert report["accepted"] is False
    assert report["stages"][-1]["id"] == "operator_profile_quick"
    assert report["stages"][-1]["ready"] is False
    assert "fix profile" in report["next_steps"]


def test_operator_acceptance_cli_require_accepted_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        operator_acceptance,
        "run_operator_acceptance",
        lambda **_kwargs: {
            "status": "needs_attention",
            "accepted": False,
            "stages": [],
            "next_steps": ["fix acceptance"],
        },
    )

    assert operator_acceptance.main(["--require-accepted"]) == 2


def test_operator_acceptance_cli_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        operator_acceptance,
        "run_operator_acceptance",
        lambda **_kwargs: {
            "status": "accepted",
            "accepted": True,
            "stages": [],
            "next_steps": [],
        },
    )

    exit_code = operator_acceptance.main(["--json", "--require-accepted"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["accepted"] is True


def _onboarding(ready: bool):
    return SimpleNamespace(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checks=[
            SimpleNamespace(id="env_template", ready=ready, required=True),
            SimpleNamespace(id="operator_acceptance_script", ready=True, required=True),
        ],
        next_steps=[] if ready else ["fix onboarding"],
    )


def _security(ready: bool):
    return SimpleNamespace(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checks=[
            SimpleNamespace(id="jwt_secret", ready=ready),
            SimpleNamespace(id="default_users", ready=True),
        ],
        next_steps=[] if ready else ["rotate secret"],
    )


def _event_store(ready: bool):
    return SimpleNamespace(
        backend="sqlite" if ready else "json",
        status="ready" if ready else "migration_available",
        ready=ready,
        event_count=3 if ready else 0,
        stream_count=2 if ready else 0,
        migration_command=None if ready else "run migration",
        warnings=[] if ready else ["event store is not durable"],
        checks=[
            SimpleNamespace(id="backend", ready=ready),
            SimpleNamespace(id="migration_script", ready=True),
        ],
    )


def _final(accepted: bool) -> dict:
    return {
        "status": "accepted" if accepted else "needs_attention",
        "accepted": accepted,
        "items": [
            {"id": "multi_agent_review_loop", "required": True, "status": "passed"},
            {"id": "approval_git_workflow", "required": True, "status": "passed" if accepted else "failed"},
        ],
        "next_steps": [] if accepted else ["fix final acceptance"],
    }


def _profile(ready: bool) -> dict:
    return {
        "status": "ready" if ready else "needs_attention",
        "ready": ready,
        "results": [
            {"id": "target_readiness", "required": True, "status": "passed" if ready else "failed"},
        ],
        "next_steps": [] if ready else ["fix profile"],
    }
