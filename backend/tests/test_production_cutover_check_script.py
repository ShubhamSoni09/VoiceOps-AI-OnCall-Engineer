from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
PREPARE_PATH = SCRIPTS_ROOT / "prepare_production_trial.py"
CUTOVER_PATH = SCRIPTS_ROOT / "production_cutover_check.py"

PREPARE_SPEC = importlib.util.spec_from_file_location("prepare_production_trial", PREPARE_PATH)
prepare_production_trial = importlib.util.module_from_spec(PREPARE_SPEC)
assert PREPARE_SPEC.loader is not None
sys.modules[PREPARE_SPEC.name] = prepare_production_trial
PREPARE_SPEC.loader.exec_module(prepare_production_trial)

CUTOVER_SPEC = importlib.util.spec_from_file_location("production_cutover_check", CUTOVER_PATH)
production_cutover_check = importlib.util.module_from_spec(CUTOVER_SPEC)
assert CUTOVER_SPEC.loader is not None
sys.modules[CUTOVER_SPEC.name] = production_cutover_check
CUTOVER_SPEC.loader.exec_module(production_cutover_check)


def test_production_cutover_passes_after_bootstrap_secret_is_deleted_and_hf_token_is_replaced(tmp_path):
    workspace = _git_workspace(tmp_path)
    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )
    env_path = Path(bundle.env_path)
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "HF_TOKEN=replace-with-huggingface-token-with-pyannote-access",
            "HF_TOKEN=hf_real_test_token",
        ),
        encoding="utf-8",
    )
    Path(bundle.bootstrap_path).unlink()

    report = production_cutover_check.run_production_cutover_check(env_file=env_path)
    checks = {check["id"]: check for check in report["checks"]}

    assert report["ready"] is True
    assert report["status"] == "ready"
    assert checks["bootstrap_secret_removed"]["ready"] is True
    assert checks["real_users"]["evidence"]["real_admin_count"] == 1
    assert checks["generated_secrets"]["evidence"]["secrets_distinct"] is True
    assert "production_trial_acceptance.py" in report["acceptance_command"]


def test_production_cutover_runtime_report_uses_active_settings(tmp_path):
    workspace = _git_workspace(tmp_path)
    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )
    env_path = Path(bundle.env_path)
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "HF_TOKEN=replace-with-huggingface-token-with-pyannote-access",
            "HF_TOKEN=hf_real_test_token",
        ),
        encoding="utf-8",
    )
    Path(bundle.bootstrap_path).unlink()
    settings = production_cutover_check._settings_from_env_file(env_path)

    report = production_cutover_check.build_production_cutover_report(settings).model_dump(mode="json")
    checks = {check["id"]: check for check in report["checks"]}

    assert report["ready"] is True
    assert checks["runtime_settings_loaded"]["ready"] is True
    assert report["env_file"] is None
    assert "../voiceops-production-trial/.env" in report["acceptance_command"]


def test_production_cutover_blocks_before_account_rotation_and_token_replacement(tmp_path):
    workspace = _git_workspace(tmp_path)
    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )

    report = production_cutover_check.run_production_cutover_check(env_file=bundle.env_path)
    checks = {check["id"]: check for check in report["checks"]}

    assert report["ready"] is False
    assert report["status"] == "needs_attention"
    assert checks["bootstrap_secret_removed"]["ready"] is False
    assert checks["hf_token_replaced"]["ready"] is False
    assert any("delete bootstrap-admin.txt" in step for step in report["next_steps"])
    assert any("HF_TOKEN" in step for step in report["next_steps"])


def test_production_cutover_blocks_seeded_demo_users(tmp_path):
    workspace = _git_workspace(tmp_path)
    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )
    users_path = Path(bundle.users_path)
    users = json.loads(users_path.read_text(encoding="utf-8"))
    users["users"].append({
        "id": "demo-admin",
        "email": "admin@voiceops.dev",
        "name": "Demo Admin",
        "role": "admin",
        "password_hash": "hash",
    })
    users_path.write_text(json.dumps(users) + "\n", encoding="utf-8")

    report = production_cutover_check.run_production_cutover_check(env_file=bundle.env_path)
    real_users = next(check for check in report["checks"] if check["id"] == "real_users")

    assert real_users["ready"] is False
    assert "admin@voiceops.dev" in real_users["summary"]


def test_production_cutover_cli_returns_nonzero_when_required_check_blocks(tmp_path, capsys):
    workspace = _git_workspace(tmp_path)
    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )

    exit_code = production_cutover_check.main(["--env-file", bundle.env_path, "--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["ready"] is False


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (workspace / "app.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=workspace,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return workspace
