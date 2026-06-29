from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "prepare_production_trial.py"
SPEC = importlib.util.spec_from_file_location("prepare_production_trial", SCRIPT_PATH)
prepare_production_trial = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = prepare_production_trial
SPEC.loader.exec_module(prepare_production_trial)


def test_create_bundle_generates_private_production_inputs_without_demo_users(tmp_path):
    workspace = _git_workspace(tmp_path)
    demo_source = tmp_path / "demo_evidence.source.json"
    demo_source.write_text(
        json.dumps({"records": [{"id": "mock_e2e", "status": "passed"}]}) + "\n",
        encoding="utf-8",
    )

    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
        agent_name="OpsMate",
        demo_evidence_source=demo_source,
    )

    env_text = Path(bundle.env_path).read_text(encoding="utf-8")
    users = json.loads(Path(bundle.users_path).read_text(encoding="utf-8"))
    demo_evidence = json.loads(Path(bundle.demo_evidence_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(bundle.manifest_path).read_text(encoding="utf-8"))
    emails = {item["email"] for item in users["users"]}

    assert emails == {"admin@example.com"}
    assert demo_evidence["records"][0]["id"] == "mock_e2e"
    assert "voiceops.dev" not in json.dumps(users)
    assert "temporary_password" not in json.dumps(manifest)
    assert "JWT_SECRET=" not in json.dumps(manifest)
    assert "JWT_SECRET=replace-with" not in env_text
    assert "EXTERNAL_AGENT_CREDENTIAL_SECRET=replace-with" not in env_text
    assert "CORS_ALLOWED_ORIGINS=https://voiceops.example.com" in env_text
    assert "SEED_DEMO_USERS=false" in env_text
    assert "PRODUCTION_STARTUP_SECURITY_GATE=true" in env_text
    assert "EXTERNAL_AGENT_OAUTH_MOCK_ENABLED=false" in env_text
    assert f"VOICEOPS_WORKSPACE={workspace}" in env_text
    data_dir = Path(bundle.output_dir) / "data"
    assert f"COLLAB_SQLITE_PATH={data_dir / 'collaboration.sqlite3'}" in env_text
    assert f"SPEAKER_SQLITE_PATH={data_dir / 'speakers.sqlite3'}" in env_text
    assert f"AGENT_RUNS_PATH={data_dir / 'agent_runs.json'}" in env_text
    assert f"AGENT_LLM_ROUTES_PATH={data_dir / 'agent_llm_routes.json'}" in env_text
    assert f"LONG_MEMORY_PATH={data_dir / 'long_memory.json'}" in env_text
    assert f"RAG_INDEX_PATH={data_dir / 'rag_index.json'}" in env_text
    assert f"LLM_CONNECTION_STORE_PATH={data_dir / 'llm_connections.json'}" in env_text
    assert f"EXTERNAL_AGENT_STORE_PATH={data_dir / 'external_agent_credentials.json'}" in env_text
    assert f"DEMO_EVIDENCE_PATH={data_dir / 'demo_evidence.json'}" in env_text
    assert "AGENT_DISPLAY_NAME=OpsMate" in env_text
    next_steps = Path(bundle.next_steps_path).read_text(encoding="utf-8")
    assert "production_trial_acceptance.py --env-file" in next_steps
    assert "Rotate the admin password" in next_steps
    assert "bundle-manifest.json" in next_steps
    assert manifest["schema_version"] == 1
    assert manifest["security"]["seed_demo_users"] is False
    assert manifest["security"]["production_startup_security_gate"] is True
    assert manifest["security"]["raw_secrets_in_manifest"] is False
    assert manifest["stores"]["relative_runtime_paths"] is False
    assert manifest["files"]["env"]["mode"] == "0o600"
    assert manifest["files"]["users"]["mode"] == "0o600"
    assert manifest["files"]["demo_evidence"]["mode"] == "0o644"
    assert manifest["files"]["bootstrap"]["mode"] == "0o600"
    assert _mode(data_dir) == 0o700
    assert _mode(bundle.env_path) == 0o600
    assert _mode(bundle.users_path) == 0o600
    assert _mode(bundle.demo_evidence_path) == 0o644
    assert _mode(bundle.bootstrap_path) == 0o600
    assert _mode(bundle.manifest_path) == 0o644


def test_generated_bundle_passes_security_gate_after_hf_token_is_set(tmp_path):
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

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_ROOT / "security_readiness.py"),
            "--env-file",
            str(env_path),
            "--json",
            "--require-ready",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    assert output["ready"] is True


def test_create_bundle_requires_absolute_workspace(tmp_path):
    try:
        prepare_production_trial.create_bundle(
            output_dir=tmp_path / "bundle",
            frontend_origin="https://voiceops.example.com",
            workspace=Path("relative/repo"),
            admin_email="admin@example.com",
            admin_name="Riley Admin",
        )
    except ValueError as exc:
        assert "absolute path" in str(exc)
    else:
        raise AssertionError("relative workspace should be rejected")


def test_create_bundle_requires_git_workspace_by_default(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()

    try:
        prepare_production_trial.create_bundle(
            output_dir=tmp_path / "bundle",
            frontend_origin="https://voiceops.example.com",
            workspace=workspace,
            admin_email="admin@example.com",
            admin_name="Riley Admin",
        )
    except ValueError as exc:
        assert "local git repository" in str(exc)
    else:
        raise AssertionError("non-git workspace should be rejected")


def test_create_bundle_can_explicitly_allow_non_git_workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()

    bundle = prepare_production_trial.create_bundle(
        output_dir=tmp_path / "bundle",
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
        allow_non_git_workspace=True,
    )

    assert Path(bundle.env_path).exists()


def test_create_bundle_rejects_runtime_inside_workspace(tmp_path):
    workspace = _git_workspace(tmp_path)

    try:
        prepare_production_trial.create_bundle(
            output_dir=workspace / "voiceops-production-trial",
            frontend_origin="https://voiceops.example.com",
            workspace=workspace,
            admin_email="admin@example.com",
            admin_name="Riley Admin",
        )
    except ValueError as exc:
        assert "outside VOICEOPS_WORKSPACE" in str(exc)
    else:
        raise AssertionError("runtime bundle inside workspace should be rejected")


def test_create_bundle_rejects_demo_admin_email(tmp_path):
    workspace = _git_workspace(tmp_path)

    try:
        prepare_production_trial.create_bundle(
            output_dir=tmp_path / "bundle",
            frontend_origin="https://voiceops.example.com",
            workspace=workspace,
            admin_email="admin@voiceops.dev",
            admin_name="Demo Admin",
        )
    except ValueError as exc:
        assert "seeded demo" in str(exc)
    else:
        raise AssertionError("demo admin email should be rejected")


def test_create_bundle_rejects_frontend_origin_with_path(tmp_path):
    workspace = _git_workspace(tmp_path)

    try:
        prepare_production_trial.create_bundle(
            output_dir=tmp_path / "bundle",
            frontend_origin="https://voiceops.example.com/app",
            workspace=workspace,
            admin_email="admin@example.com",
            admin_name="Riley Admin",
        )
    except ValueError as exc:
        assert "origin only" in str(exc)
    else:
        raise AssertionError("frontend origin with path should be rejected")


def test_create_bundle_refuses_to_overwrite_without_force(tmp_path):
    workspace = _git_workspace(tmp_path)
    output_dir = tmp_path / "bundle"
    prepare_production_trial.create_bundle(
        output_dir=output_dir,
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )

    try:
        prepare_production_trial.create_bundle(
            output_dir=output_dir,
            frontend_origin="https://voiceops.example.com",
            workspace=workspace,
            admin_email="admin@example.com",
            admin_name="Riley Admin",
        )
    except ValueError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("existing bundle should not be overwritten without --force")


def test_create_bundle_force_overwrites_existing_bundle(tmp_path):
    workspace = _git_workspace(tmp_path)
    output_dir = tmp_path / "bundle"
    first = prepare_production_trial.create_bundle(
        output_dir=output_dir,
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
    )
    first_env = Path(first.env_path).read_text(encoding="utf-8")

    second = prepare_production_trial.create_bundle(
        output_dir=output_dir,
        frontend_origin="https://voiceops.example.com",
        workspace=workspace,
        admin_email="admin@example.com",
        admin_name="Riley Admin",
        force=True,
    )
    second_env = Path(second.env_path).read_text(encoding="utf-8")

    assert second.force is True
    assert first_env != second_env


def test_prepare_production_trial_cli_prints_json(tmp_path, capsys):
    workspace = _git_workspace(tmp_path)

    exit_code = prepare_production_trial.main([
        "--output-dir",
        str(tmp_path / "bundle"),
        "--frontend-origin",
        "https://voiceops.example.com",
        "--workspace",
        str(workspace),
        "--admin-email",
        "admin@example.com",
        "--admin-name",
        "Riley Admin",
        "--json",
    ])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["admin_email"] == "admin@example.com"
    assert Path(output["env_path"]).exists()
    assert Path(output["demo_evidence_path"]).exists()


def _mode(path: str) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return workspace
