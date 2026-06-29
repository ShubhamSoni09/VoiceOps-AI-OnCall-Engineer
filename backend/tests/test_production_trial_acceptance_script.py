from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "production_trial_acceptance.py"
SPEC = importlib.util.spec_from_file_location("production_trial_acceptance", SCRIPT_PATH)
production_trial_acceptance = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = production_trial_acceptance
SPEC.loader.exec_module(production_trial_acceptance)


def test_production_trial_acceptance_initializes_event_store_and_aggregates_ready_reports(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path)
    (tmp_path / "bootstrap-admin.txt").unlink()
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is True
    assert report["status"] == "accepted"
    assert stages["event_store"]["ready"] is True
    assert stages["bundle_manifest"]["ready"] is True
    assert "initialized=True" in stages["event_store"]["evidence"]
    assert (tmp_path / "runtime" / "collaboration.sqlite3").exists()
    assert stages["security_readiness"]["ready"] is True
    assert stages["startup_security_gate"]["ready"] is True
    assert "startup_gate=passed" in stages["startup_security_gate"]["evidence"]
    assert stages["deployment_hardening"]["ready"] is True
    assert stages["final_acceptance"]["ready"] is True


def test_production_trial_acceptance_reports_blocking_security_and_deployment_steps(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path)
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(False))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(False))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )

    assert report["accepted"] is False
    assert report["status"] == "needs_attention"
    assert "rotate secret" in report["next_steps"]
    assert "delete bootstrap-admin.txt" in report["next_steps"]


def test_production_trial_acceptance_requires_startup_gate_enabled(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path, extra=["PRODUCTION_STARTUP_SECURITY_GATE=false"])
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["startup_security_gate"]["ready"] is False
    assert "startup_gate=not_enforced" in stages["startup_security_gate"]["evidence"]
    assert any("PRODUCTION_STARTUP_SECURITY_GATE=true" in step for step in report["next_steps"])


def test_production_trial_acceptance_requires_production_environment(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path, deployment_environment="staging")
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["startup_security_gate"]["ready"] is False
    assert "environment=staging" in stages["startup_security_gate"]["evidence"]
    assert any("DEPLOYMENT_ENVIRONMENT=production" in step for step in report["next_steps"])


def test_production_trial_acceptance_resolves_relative_runtime_paths_against_env_file(tmp_path):
    env_dir = tmp_path / "bundle"
    env_dir.mkdir()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    env_path = env_dir / ".env"
    env_path.write_text(
        "\n".join(
            [
                "DEPLOYMENT_ENVIRONMENT=production",
                "USERS_STORE_PATH=data/users.json",
                "COLLAB_STORE_BACKEND=sqlite",
                "COLLAB_SQLITE_PATH=data/collaboration.sqlite3",
                f"VOICEOPS_WORKSPACE={workspace}",
                "AGENT_RUNS_PATH=data/agent_runs.json",
                "MEMORY_STORE_PATH=.voiceops_memory.json",
            ]
        ),
        encoding="utf-8",
    )

    settings = production_trial_acceptance._settings_from_env_file(env_path)

    assert settings.users_store_path == env_dir / "data" / "users.json"
    assert settings.collab_sqlite_path == env_dir / "data" / "collaboration.sqlite3"
    assert settings.agent_runs_path == env_dir / "data" / "agent_runs.json"
    assert settings.memory_store_path == str(env_dir / ".voiceops_memory.json")


def test_production_trial_acceptance_blocks_missing_bundle_manifest(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path, with_manifest=False)
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["bundle_manifest"]["ready"] is False
    assert stages["bundle_manifest"]["status"] == "missing"
    assert any("prepare_production_trial.py" in step for step in report["next_steps"])


def test_production_trial_acceptance_blocks_bundle_manifest_drift(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path)
    manifest_path = env_path.parent / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["workspace_path"] = "/tmp/not-the-configured-workspace"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["bundle_manifest"]["ready"] is False
    assert "workspace_path" in stages["bundle_manifest"]["summary"]


def test_production_trial_acceptance_allows_deleted_bootstrap_secret_file(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path)
    (tmp_path / "bootstrap-admin.txt").unlink()
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is True
    assert stages["bundle_manifest"]["ready"] is True
    assert "bootstrap_file_recorded=True" in stages["bundle_manifest"]["evidence"]
    assert "bootstrap_deleted_or_private=True" in stages["bundle_manifest"]["evidence"]
    assert "bootstrap_deleted=True" in stages["bundle_manifest"]["evidence"]


def test_production_trial_acceptance_blocks_existing_bootstrap_secret_file(tmp_path, monkeypatch):
    env_path = _env_file(tmp_path)
    monkeypatch.setattr(production_trial_acceptance, "run_security_readiness", lambda **_kwargs: _security(True))
    monkeypatch.setattr(production_trial_acceptance, "build_deployment_hardening_report", lambda _settings: _deployment(True))
    monkeypatch.setattr(production_trial_acceptance, "run_final_acceptance_audit", lambda **_kwargs: _final(True))

    report = production_trial_acceptance.run_production_trial_acceptance(
        env_file=env_path,
        project_root=tmp_path,
    )
    stages = {stage["id"]: stage for stage in report["stages"]}

    assert report["accepted"] is False
    assert stages["bundle_manifest"]["ready"] is False
    assert "bootstrap_deleted" in stages["bundle_manifest"]["summary"]


def test_production_trial_acceptance_cli_returns_nonzero_when_required_stage_blocks(tmp_path, monkeypatch, capsys):
    env_path = _env_file(tmp_path)
    monkeypatch.setattr(
        production_trial_acceptance,
        "run_production_trial_acceptance",
        lambda **_kwargs: {
            "status": "needs_attention",
            "accepted": False,
            "env_file": str(env_path),
            "run_harnesses": True,
            "duration_ms": 1,
            "stages": [],
            "next_steps": ["fix production config"],
        },
    )

    exit_code = production_trial_acceptance.main(["--env-file", str(env_path), "--json", "--require-accepted"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["accepted"] is False
    assert output["next_steps"] == ["fix production config"]


def _env_file(
    tmp_path: Path,
    *,
    deployment_environment: str = "production",
    extra: list[str] | None = None,
    with_manifest: bool = True,
) -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"DEPLOYMENT_ENVIRONMENT={deployment_environment}",
                "JWT_SECRET=" + "x" * 40,
                "CORS_ALLOWED_ORIGINS=https://voiceops.example.com",
                f"USERS_STORE_PATH={runtime_dir / 'users.json'}",
                "SEED_DEMO_USERS=false",
                "COLLAB_STORE_BACKEND=sqlite",
                f"COLLAB_SQLITE_PATH={runtime_dir / 'collaboration.sqlite3'}",
                "SPEAKER_STORE_BACKEND=sqlite",
                f"VOICEOPS_WORKSPACE={workspace}",
                f"AGENT_RUNS_PATH={runtime_dir / 'agent_runs.json'}",
                f"AGENT_LLM_ROUTES_PATH={runtime_dir / 'agent_llm_routes.json'}",
                f"LONG_MEMORY_PATH={runtime_dir / 'long_memory.json'}",
                f"RAG_INDEX_PATH={runtime_dir / 'rag_index.json'}",
                f"LLM_CONNECTION_STORE_PATH={runtime_dir / 'llm_connections.json'}",
                f"EXTERNAL_AGENT_STORE_PATH={runtime_dir / 'external_agent_credentials.json'}",
                f"DEMO_EVIDENCE_PATH={runtime_dir / 'demo_evidence.json'}",
                "SPEAKER_PROVIDER=whisperx",
                "HF_TOKEN=hf_test_token",
                "EXTERNAL_AGENT_CREDENTIAL_SECRET=" + "y" * 40,
                "EXTERNAL_AGENT_OAUTH_MOCK_ENABLED=false",
                "GITHUB_PR_CREATION_ENABLED=false",
                *(extra or []),
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(env_path, 0o600)
    users_path = runtime_dir / "users.json"
    users_path.write_text('{"users": []}\n', encoding="utf-8")
    os.chmod(users_path, 0o600)
    demo_evidence_path = runtime_dir / "demo_evidence.json"
    demo_evidence_path.write_text('{"records": []}\n', encoding="utf-8")
    os.chmod(demo_evidence_path, 0o644)
    bootstrap_path = tmp_path / "bootstrap-admin.txt"
    bootstrap_path.write_text("temporary password goes here\n", encoding="utf-8")
    os.chmod(bootstrap_path, 0o600)
    next_steps_path = tmp_path / "README_NEXT_STEPS.md"
    next_steps_path.write_text("next steps\n", encoding="utf-8")
    os.chmod(next_steps_path, 0o644)
    if with_manifest:
        startup_gate = "PRODUCTION_STARTUP_SECURITY_GATE=false" not in (extra or [])
        (tmp_path / "bundle-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_by": "scripts/prepare_production_trial.py",
                    "frontend_origin": "https://voiceops.example.com",
                    "workspace_path": str(workspace),
                    "files": {
                        "env": {"path": str(env_path), "mode": "0o600"},
                        "users": {"path": str(users_path), "mode": "0o600"},
                        "demo_evidence": {"path": str(demo_evidence_path), "mode": "0o644"},
                        "bootstrap": {"path": str(bootstrap_path), "mode": "0o600"},
                        "next_steps": {"path": str(next_steps_path), "mode": "0o644"},
                    },
                    "security": {
                        "seed_demo_users": False,
                        "production_startup_security_gate": startup_gate,
                        "raw_secrets_in_manifest": False,
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(tmp_path / "bundle-manifest.json", 0o644)
    return env_path


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


def _deployment(ready: bool):
    return SimpleNamespace(
        status="ready" if ready else "needs_attention",
        ready=ready,
        ready_count=2 if ready else 1,
        total_count=2,
        checks=[
            SimpleNamespace(id="event_store_readiness", ready=True),
            SimpleNamespace(id="bootstrap_secret_file", ready=ready),
        ],
        next_steps=[] if ready else ["delete bootstrap-admin.txt"],
    )


def _final(ready: bool):
    return {
        "status": "accepted" if ready else "needs_attention",
        "accepted": ready,
        "items": [
            {"id": "multi_speaker_shared_room", "required": True, "status": "passed" if ready else "failed"},
            {"id": "approval_git_workflow", "required": True, "status": "passed"},
        ],
        "next_steps": [] if ready else ["fix final acceptance"],
    }
