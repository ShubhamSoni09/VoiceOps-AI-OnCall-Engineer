import importlib.util
import json
import sys


SCRIPTS_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "security_readiness.py"
SPEC = importlib.util.spec_from_file_location("security_readiness", SCRIPT_PATH)
security_readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = security_readiness
SPEC.loader.exec_module(security_readiness)


def _report(ready=True):
    return security_readiness.ProductionSecurityReport(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checked_at="2026-06-19T00:00:00+00:00",
        environment="production",
        checks=[],
        threats=[],
        requirements=[],
        next_steps=[] if ready else ["Set JWT_SECRET"],
    )


def test_security_readiness_script_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(security_readiness, "run_security_readiness", lambda **_kwargs: _report(ready=True))

    exit_code = security_readiness.main(["--json", "--require-ready"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"status": "ready"' in output


def test_security_readiness_script_require_ready_fails_when_blocked(monkeypatch):
    monkeypatch.setattr(security_readiness, "run_security_readiness", lambda **_kwargs: _report(ready=False))

    assert security_readiness.main(["--require-ready"]) == 2


def test_security_readiness_script_default_is_diagnostic(monkeypatch):
    monkeypatch.setattr(security_readiness, "run_security_readiness", lambda **_kwargs: _report(ready=False))

    assert security_readiness.main([]) == 0


def test_security_readiness_script_can_validate_env_file(tmp_path, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "DEPLOYMENT_ENVIRONMENT=production",
                "JWT_SECRET=" + "x" * 40,
                "CORS_ALLOWED_ORIGINS=https://voiceops.example.com",
                f"USERS_STORE_PATH={users_path}",
                "SEED_DEMO_USERS=false",
                "COLLAB_STORE_BACKEND=sqlite",
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
                "HF_TOKEN=hf_real_test_token",
                "EXTERNAL_AGENT_CREDENTIAL_SECRET=" + "y" * 40,
                "EXTERNAL_AGENT_OAUTH_MOCK_ENABLED=false",
                "GITHUB_PR_CREATION_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = security_readiness.main(["--env-file", str(env_path), "--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ready"] is True
    assert output["environment"] == "production"


def test_security_readiness_script_rejects_placeholder_env_file_secrets(tmp_path, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "DEPLOYMENT_ENVIRONMENT=production",
                "JWT_SECRET=replace-with-64-hex-character-secret",
                "CORS_ALLOWED_ORIGINS=https://voiceops.example.com",
                f"USERS_STORE_PATH={users_path}",
                "SEED_DEMO_USERS=false",
                "COLLAB_STORE_BACKEND=sqlite",
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
                "HF_TOKEN=replace-with-huggingface-token-with-pyannote-access",
                "EXTERNAL_AGENT_CREDENTIAL_SECRET=replace-with-64-hex-character-secret",
                "GITHUB_PR_CREATION_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = security_readiness.main(["--env-file", str(env_path), "--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)
    checks = {check["id"]: check for check in output["checks"]}

    assert exit_code == 2
    assert checks["jwt_secret"]["ready"] is False
    assert checks["speaker_provider"]["ready"] is False
    assert checks["external_agent_credentials"]["ready"] is False


def test_security_readiness_script_rejects_runtime_artifacts_in_workspace(tmp_path, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    users_path = tmp_path / "users.json"
    users_path.write_text('{"users":[]}', encoding="utf-8")
    users_path.chmod(0o600)
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "DEPLOYMENT_ENVIRONMENT=production",
                "JWT_SECRET=" + "x" * 40,
                "CORS_ALLOWED_ORIGINS=https://voiceops.example.com",
                f"USERS_STORE_PATH={users_path}",
                "SEED_DEMO_USERS=false",
                "COLLAB_STORE_BACKEND=sqlite",
                "SPEAKER_STORE_BACKEND=sqlite",
                f"VOICEOPS_WORKSPACE={workspace}",
                f"AGENT_RUNS_PATH={workspace / 'agent_runs.json'}",
                f"AGENT_LLM_ROUTES_PATH={runtime_dir / 'agent_llm_routes.json'}",
                f"LONG_MEMORY_PATH={runtime_dir / 'long_memory.json'}",
                f"RAG_INDEX_PATH={runtime_dir / 'rag_index.json'}",
                f"LLM_CONNECTION_STORE_PATH={runtime_dir / 'llm_connections.json'}",
                f"EXTERNAL_AGENT_STORE_PATH={runtime_dir / 'external_agent_credentials.json'}",
                f"DEMO_EVIDENCE_PATH={runtime_dir / 'demo_evidence.json'}",
                "SPEAKER_PROVIDER=whisperx",
                "HF_TOKEN=hf_real_test_token",
                "EXTERNAL_AGENT_CREDENTIAL_SECRET=" + "y" * 40,
                "GITHUB_PR_CREATION_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = security_readiness.main(["--env-file", str(env_path), "--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)
    checks = {check["id"]: check for check in output["checks"]}

    assert exit_code == 2
    assert checks["runtime_artifact_paths"]["ready"] is False
    assert checks["runtime_artifact_paths"]["evidence"]["artifacts"]["agent_runs"]["in_workspace"] is True
