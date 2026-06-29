from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "final_acceptance_audit.py"
SPEC = importlib.util.spec_from_file_location("final_acceptance_audit", SCRIPT_PATH)
final_acceptance_audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = final_acceptance_audit
SPEC.loader.exec_module(final_acceptance_audit)


def test_final_acceptance_audit_passes_with_current_repo():
    report = final_acceptance_audit.run_final_acceptance_audit(run_harnesses=True)

    assert report["accepted"] is True
    assert report["status"] == "accepted"
    item_ids = {item["id"] for item in report["items"]}
    assert "source_audits_integrity" in item_ids
    assert "multi_speaker_shared_room" in item_ids
    assert "multi_agent_review_loop" in item_ids
    assert "external_agent_providers" in item_ids
    assert "source_tree_hygiene" in item_ids
    assert "production_trial_packaging" in item_ids
    source = next(item for item in report["items"] if item["id"] == "source_audits_integrity")
    assert source["label"] == "Product audit, target diagnostics, and onboarding evidence are coherent"
    assert any("Target readiness:" in item for item in source["evidence"])
    packaging = next(item for item in report["items"] if item["id"] == "production_trial_packaging")
    assert any("bootstrap_local_runtime.py" in item for item in packaging["evidence"])
    assert any("test_production_trial_acceptance_script.py" in item for item in packaging["evidence"])
    assert all(item["status"] == "passed" for item in report["items"])
    assert report["production_deployment_prerequisites"]
    assert any("prepare_production_trial.py" in step for step in report["next_steps"])
    assert any("production_cutover_check.py" in step for step in report["next_steps"])


def test_final_acceptance_audit_fails_when_required_product_item_is_blocked(monkeypatch):
    product = _product_report()
    product["status"] = "needs_attention"
    product["ready"] = False
    product["next_steps"] = ["rerun product audit"]
    product["items"][0]["status"] = "failed"
    product["items"][0]["gaps"] = ["real browser live missing"]
    monkeypatch.setattr(final_acceptance_audit, "run_product_audit", lambda **_kwargs: product)
    monkeypatch.setattr(final_acceptance_audit, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(final_acceptance_audit, "run_security_readiness", lambda _settings: _security())

    report = final_acceptance_audit.run_final_acceptance_audit(run_harnesses=False)

    assert report["accepted"] is False
    assert report["status"] == "needs_attention"
    assert any("real browser live missing" in step for step in report["next_steps"])
    assert any("rerun product audit" in step for step in report["next_steps"])


def test_final_acceptance_accepts_only_bootstrappable_target_diagnostics(monkeypatch):
    product = _product_report()
    product["target_readiness"] = {
        "status": "needs_attention",
        "ready": False,
        "next_steps": ["Run the local runtime bootstrap or migrate collaboration memory to SQLite event-store mode."],
        "milestones": [
            _milestone("workspace_git", "Workspace + git workflow"),
            _milestone("approval_git_tests", "Approval + branch + tests"),
            _milestone("real_multi_speaker", "Real 2+ speaker diarization"),
            _milestone("real_browser_live", "Browser mic to speaker labels"),
            {
                "id": "durable_event_store",
                "label": "Durable event store",
                "ready": False,
                "status": "migration_available",
                "detail": "json backend is usable for local development.",
                "next_action": "Run the local runtime bootstrap or migrate collaboration memory to SQLite event-store mode.",
                "command": "cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json",
            },
        ],
    }
    monkeypatch.setattr(final_acceptance_audit, "run_product_audit", lambda **_kwargs: product)
    monkeypatch.setattr(final_acceptance_audit, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(final_acceptance_audit, "run_security_readiness", lambda _settings: _security())
    monkeypatch.setattr(final_acceptance_audit, "_run_external_agent_smoke", lambda: _external_smoke())

    report = final_acceptance_audit.run_final_acceptance_audit(run_harnesses=True, project_root=Path.cwd())
    source = next(item for item in report["items"] if item["id"] == "source_audits_integrity")

    assert report["accepted"] is True
    assert source["status"] == "passed"
    assert any("diagnostic blockers accepted: durable_event_store" in item for item in source["evidence"])
    assert any("bootstrap_local_runtime.py" in item for item in source["evidence"])


def test_final_acceptance_requires_unified_external_bridge_evidence(monkeypatch):
    product = _product_report()
    bridge = next(item for item in product["items"] if item["id"] == "external_coding_agent_bridge")
    bridge["evidence"] = ["provider=codex runtime=local_cli_patch/temporary branch=voiceops/act-codex tests=True"]
    monkeypatch.setattr(final_acceptance_audit, "run_product_audit", lambda **_kwargs: product)
    monkeypatch.setattr(final_acceptance_audit, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(final_acceptance_audit, "run_security_readiness", lambda _settings: _security())
    monkeypatch.setattr(final_acceptance_audit, "_run_external_agent_smoke", lambda: _external_smoke())

    report = final_acceptance_audit.run_final_acceptance_audit(run_harnesses=True, project_root=Path.cwd())
    item = next(item for item in report["items"] if item["id"] == "external_agent_providers")

    assert report["accepted"] is False
    assert item["status"] == "failed"
    assert any("provider=local" in gap for gap in item["gaps"])


def test_final_acceptance_requires_source_tree_hygiene(monkeypatch):
    monkeypatch.setattr(final_acceptance_audit, "run_product_audit", lambda **_kwargs: _product_report())
    monkeypatch.setattr(final_acceptance_audit, "run_team_onboarding_check", lambda **_kwargs: _onboarding(True))
    monkeypatch.setattr(final_acceptance_audit, "run_security_readiness", lambda _settings: _security())
    monkeypatch.setattr(final_acceptance_audit, "_run_external_agent_smoke", lambda: _external_smoke())
    monkeypatch.setattr(
        final_acceptance_audit,
        "run_source_tree_hygiene",
        lambda _root: {
            "status": "failed",
            "ready": False,
            "items": [{"label": "No generated/runtime artifacts are visible to git", "status": "failed"}],
            "next_steps": ["Generated/runtime artifact is visible to git: backend/data/users.sqlite3"],
        },
    )

    report = final_acceptance_audit.run_final_acceptance_audit(run_harnesses=True, project_root=Path.cwd())
    item = next(item for item in report["items"] if item["id"] == "source_tree_hygiene")

    assert report["accepted"] is False
    assert item["status"] == "failed"
    assert any("backend/data/users.sqlite3" in gap for gap in item["gaps"])


def test_final_acceptance_audit_require_accepted_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        final_acceptance_audit,
        "run_final_acceptance_audit",
        lambda run_harnesses=True: {
            "status": "needs_attention",
            "accepted": False,
            "items": [],
            "product_audit": {},
            "onboarding": {},
            "production_deployment_prerequisites": [],
            "next_steps": ["fix product audit"],
        },
    )

    assert final_acceptance_audit.main(["--require-accepted"]) == 2


def test_final_acceptance_audit_script_loads_env_file(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    env_path = tmp_path / ".env.production"
    env_path.write_text(f"VOICEOPS_WORKSPACE={workspace}\n", encoding="utf-8")
    captured = {}

    def fake_final_acceptance(*, run_harnesses=True, settings=None, project_root=None):
        captured["run_harnesses"] = run_harnesses
        captured["workspace"] = settings.voiceops_workspace
        return {
            "status": "accepted",
            "accepted": True,
            "run_harnesses": run_harnesses,
            "items": [],
            "product_audit": {},
            "onboarding": {},
            "production_deployment_prerequisites": [],
            "next_steps": [],
        }

    monkeypatch.setattr(final_acceptance_audit, "run_final_acceptance_audit", fake_final_acceptance)

    exit_code = final_acceptance_audit.main(["--env-file", str(env_path), "--json", "--require-accepted"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["accepted"] is True
    assert captured == {"run_harnesses": True, "workspace": str(workspace)}


def _product_report() -> dict:
    items = [
        _product_item("multi_speaker_live", "Multi-speaker live meeting", ["Real 2+ speaker diarization: ready"]),
        _product_item("browser_team_ui", "Team UI, speaker mapping, and agent controls", ["mock_e2e: passed | 2 speakers"]),
        _product_item("memory_rag", "Short/long memory and cited RAG answers", ["RAG harness: provider=local_sparse, citations=3, ontology=2"]),
        _product_item("multi_agent_review_loop", "Specialist multi-agent review loop", ["roles=coordinator,code,review revision=True"]),
        _product_item(
            "external_coding_agent_bridge",
            "External coding agent bridge, local provider, and sandboxed approval",
            [
                "provider=claude runtime=local_cli_patch/temporary branch=voiceops/act-claude tests=True",
                "provider=codex runtime=local_cli_patch/temporary branch=voiceops/act-codex tests=True",
                "provider=local runtime=local_cli_patch/temporary branch=voiceops/act-local tests=True",
            ],
        ),
        _product_item("approval_git_tests", "Approval-first patch, local branch, and tests", ["closure harness: action=act-1, branch=voiceops/act-1-x, tests=True"]),
        _product_item("handoff_audit", "Returning teammate handoff and audit trail", ["Approved by Sam Ortiz", "Branch: voiceops/"]),
        _product_item("operator_readiness", "Operator command set and real-speaker preflight", ["local: demo_readiness_full"]),
    ]
    return {
        "status": "ready",
        "ready": True,
        "next_steps": [],
        "items": items,
        "target_readiness": {
            "status": "ready",
            "ready": True,
            "next_steps": [],
            "milestones": [
                _milestone("real_multi_speaker", "Real 2+ speaker diarization"),
                _milestone("real_browser_live", "Browser mic to speaker labels"),
                _milestone("approval_git_tests", "Approval + branch + tests"),
            ]
        },
    }


def _product_item(item_id: str, label: str, evidence: list[str]) -> dict:
    return {
        "id": item_id,
        "label": label,
        "status": "passed",
        "required": True,
        "evidence": evidence,
        "gaps": [],
        "command": "run command",
    }


def _milestone(item_id: str, label: str) -> dict:
    return {
        "id": item_id,
        "label": label,
        "ready": True,
        "status": "ready",
        "detail": f"{label} ready",
        "evidence": "passed | 2 speakers",
    }


def _onboarding(ready: bool):
    return SimpleNamespace(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checks=[SimpleNamespace(id="env_template")],
        next_steps=[] if ready else ["fix onboarding"],
    )


def _security():
    return SimpleNamespace(
        status="needs_attention",
        checks=[
            SimpleNamespace(
                ready=False,
                mitigations=["Set JWT_SECRET to a generated 32+ character secret in production secret storage."],
            )
        ],
    )


def _external_smoke():
    return SimpleNamespace(
        provider="cursor",
        auth_method="local_cli",
        status="pending_approval",
        action_id="act-test",
        preflight_status="needs_attention",
        provider_preflight_state="warning",
        provider_preflight_summary="Ready with 1 warning.",
        provider_preflight_blockers=[],
        runtime_evidence={
            "local_cli_runtime": "isolated_temp_workspace",
            "env_policy": "minimal_external_agent_env",
            "tokens_returned_to_browser": False,
        },
        setup_guide_state="warning",
        setup_guide_path="local_cli_for_code_changes",
        setup_guide_next_step="Enable CLI execution when you are ready to run the real local coding agent.",
        setup_guide_recommended_steps=["connect_local_cli"],
        workspace_unchanged_before_approval=True,
        diff_preview_present=True,
        token_redacted=True,
    )
