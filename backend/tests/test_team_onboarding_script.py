from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "team_onboarding_check.py"
SPEC = importlib.util.spec_from_file_location("team_onboarding_check", SCRIPT_PATH)
team_onboarding_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = team_onboarding_check
SPEC.loader.exec_module(team_onboarding_check)


def test_team_onboarding_check_passes_for_packaged_repo():
    report = team_onboarding_check.run_team_onboarding_check()

    assert report.ready is True
    assert report.status == "ready"
    ids = {check.id for check in report.checks}
    assert "env_template" in ids
    assert "team_onboarding_doc" in ids
    assert "production_trial_script" in ids
    assert "local_runtime_bootstrap_script" in ids
    assert "event_store_init_script" in ids
    assert "production_trial_acceptance_script" in ids
    assert "production_cutover_script" in ids
    assert any("product_audit.py --run-harnesses" in command for command in report.commands)
    assert any("production_trial_acceptance.py --env-file" in command for command in report.commands)
    assert any("production_cutover_check.py --env-file" in command for command in report.commands)
    assert any("product_audit.py --env-file" in command for command in report.commands)
    assert any("init_collab_event_store.py" in command for command in report.commands)
    assert any("prepare_production_trial.py" in command for command in report.commands)
    assert any("bootstrap_local_runtime.py" in command for command in report.commands)
    data = team_onboarding_check.report_to_dict(report)
    assert {check["status"] for check in data["checks"]} == {"passed"}


def test_team_onboarding_check_reports_missing_template_content(tmp_path):
    project = tmp_path / "project"
    backend = project / "backend"
    docs = project / "docs"
    scripts = backend / "scripts"
    scripts.mkdir(parents=True)
    docs.mkdir()
    (backend / ".env.production.example").write_text("JWT_SECRET=value\n", encoding="utf-8")
    (docs / "TEAM_ONBOARDING.md").write_text(
        "Configure Environment\nRotate Accounts\nStart Services\nAcceptance Flow\nTrial Script\nFailure Handling\n",
        encoding="utf-8",
    )
    (docs / "OPERATOR_RUNBOOK.md").write_text(
        "operator_acceptance.py --json\n"
        "bootstrap_local_runtime.py\n"
        "prepare_production_trial.py\n"
        "production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready\n"
        "production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted\n"
        "init_collab_event_store.py\n"
        "demo_operator.py --profile local --run --json\n"
        "product_audit.py --run-harnesses --require-ready --json\n"
        "product_audit.py --env-file ../voiceops-production-trial/.env --run-harnesses --require-ready --json\n"
        "security_readiness.py --env-file ../voiceops-production-trial/.env --require-ready --json\n",
        encoding="utf-8",
    )
    for script in (
        "security_readiness.py",
        "product_audit.py",
        "demo_operator.py",
        "operator_acceptance.py",
        "bootstrap_local_runtime.py",
        "init_collab_event_store.py",
        "production_trial_acceptance.py",
        "production_cutover_check.py",
    ):
        (scripts / script).write_text("", encoding="utf-8")

    report = team_onboarding_check.run_team_onboarding_check(project_root=project)

    assert report.ready is False
    failed = {check.id: check for check in report.checks if not check.ready}
    assert "env_template" in failed
    assert "team_onboarding_doc" in failed
    assert "operator_acceptance_flow" in failed
    assert "production_trial_script" in failed
    assert "DEPLOYMENT_ENVIRONMENT" in failed["env_template"].detail


def test_team_onboarding_check_require_ready_exits_nonzero(monkeypatch):
    blocked = team_onboarding_check.OnboardingReport(
        status="needs_attention",
        ready=False,
        checks=[],
        commands=[],
        next_steps=["fix template"],
    )
    monkeypatch.setattr(team_onboarding_check, "run_team_onboarding_check", lambda: blocked)

    assert team_onboarding_check.main(["--require-ready"]) == 2
