from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app.system.evidence import DemoEvidenceRecord, DemoEvidenceStatus
from app.system.router import TargetReadinessMilestone, TargetReadinessResponse


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "product_audit.py"
SPEC = importlib.util.spec_from_file_location("product_audit", SCRIPT_PATH)
product_audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = product_audit
SPEC.loader.exec_module(product_audit)


def test_product_audit_requires_current_code_harnesses(monkeypatch):
    monkeypatch.setattr(product_audit, "run_target_readiness", lambda _settings: _target_ready())
    monkeypatch.setattr(product_audit, "read_demo_evidence", lambda _path: _evidence_ready())

    report = product_audit.run_product_audit(run_harnesses=False)

    assert report["ready"] is False
    failed = {item["id"]: item for item in report["items"] if item["status"] != "passed"}
    assert "multi_agent_review_loop" in failed
    assert "external_coding_agent_bridge" in failed
    assert "prompt_injection_safety" in failed
    assert "approval_git_tests" in failed
    assert "handoff_audit" in failed
    assert any("smoke_multi_agent.py" in step for step in report["next_steps"])


def test_product_audit_passes_with_deterministic_harness_reports(monkeypatch):
    monkeypatch.setattr(product_audit, "run_target_readiness", lambda _settings: _target_ready())
    monkeypatch.setattr(product_audit, "read_demo_evidence", lambda _path: _evidence_ready())
    monkeypatch.setattr(product_audit, "_run_harnesses", _harnesses_ready)
    monkeypatch.setattr(product_audit, "profile_preflight", lambda _profile: [_preflight("hf_token")])

    report = product_audit.run_product_audit(run_harnesses=True)

    assert report["ready"] is True
    assert all(item["status"] == "passed" for item in report["items"])
    assert report["next_steps"] == []


def test_product_audit_accepts_closure_harness_when_workspace_target_is_unconfigured(monkeypatch):
    monkeypatch.setattr(product_audit, "run_target_readiness", lambda _settings: _target_with_unconfigured_approval())
    monkeypatch.setattr(product_audit, "read_demo_evidence", lambda _path: _evidence_ready())
    monkeypatch.setattr(product_audit, "_run_harnesses", _harnesses_ready)
    monkeypatch.setattr(product_audit, "profile_preflight", lambda _profile: [_preflight("hf_token")])

    report = product_audit.run_product_audit(run_harnesses=True)
    approval = next(item for item in report["items"] if item["id"] == "approval_git_tests")

    assert approval["status"] == "passed"
    assert approval["gaps"] == []
    assert any("still needs local workspace configuration" in item for item in approval["evidence"])


def test_product_audit_blocks_missing_workspace_git_and_event_store(monkeypatch):
    monkeypatch.setattr(product_audit, "run_target_readiness", lambda _settings: _target_without_runtime())
    monkeypatch.setattr(product_audit, "read_demo_evidence", lambda _path: _evidence_ready())
    monkeypatch.setattr(product_audit, "_run_harnesses", _harnesses_ready)
    monkeypatch.setattr(product_audit, "profile_preflight", lambda _profile: [_preflight("hf_token")])

    report = product_audit.run_product_audit(run_harnesses=True)
    runtime = next(item for item in report["items"] if item["id"] == "production_runtime")

    assert report["ready"] is False
    assert runtime["status"] == "failed"
    assert "Run the local runtime bootstrap with a git workspace." in runtime["gaps"]
    assert "Use SQLite event-store mode before production." in runtime["gaps"]


def test_product_audit_require_ready_exits_nonzero_when_blocked(monkeypatch, capsys):
    monkeypatch.setattr(product_audit, "run_product_audit", lambda run_harnesses=False: {
        "status": "needs_attention",
        "ready": False,
        "items": [],
        "next_steps": ["run smoke"],
    })

    exit_code = product_audit.main(["--require-ready"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "VoiceOps product audit: needs_attention" in output


def test_product_audit_script_loads_env_file(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    env_path = tmp_path / ".env.production"
    env_path.write_text(f"VOICEOPS_WORKSPACE={workspace}\n", encoding="utf-8")
    captured = {}

    def fake_product_audit(*, run_harnesses=False, settings=None):
        captured["run_harnesses"] = run_harnesses
        captured["workspace"] = settings.voiceops_workspace
        return {
            "status": "ready",
            "ready": True,
            "run_harnesses": run_harnesses,
            "target_readiness": {},
            "demo_evidence": {},
            "items": [],
            "next_steps": [],
        }

    monkeypatch.setattr(product_audit, "run_product_audit", fake_product_audit)

    exit_code = product_audit.main(["--env-file", str(env_path), "--run-harnesses", "--json", "--require-ready"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ready"] is True
    assert captured == {"run_harnesses": True, "workspace": str(workspace)}


def _target_ready() -> TargetReadinessResponse:
    ids = [
        ("workspace_git", "Workspace + git workflow"),
        ("ai_memory_loop", "AI teammate + meeting memory"),
        ("approval_git_tests", "Approval + branch + tests"),
        ("durable_event_store", "Durable event store"),
        ("real_multi_speaker", "Real 2+ speaker diarization"),
        ("real_live_backend", "Real WhisperX live backend"),
        ("real_browser_live", "Browser mic to speaker labels"),
    ]
    milestones = [
        TargetReadinessMilestone(
            id=item_id,
            label=label,
            ready=True,
            status="ready",
            detail=f"{label} ready",
            evidence="passed | 2 speakers | 2/2 chunks",
            command="run command",
        )
        for item_id, label in ids
    ]
    return TargetReadinessResponse(
        status="ready",
        ready=True,
        checked_at="2026-06-19T00:00:00+00:00",
        score=100,
        ready_count=len(milestones),
        total_count=len(milestones),
        milestones=milestones,
        next_steps=[],
    )


def _target_with_unconfigured_approval() -> TargetReadinessResponse:
    report = _target_ready()
    milestones = [
        item.model_copy(
            update={
                "ready": False,
                "status": "unproven",
                "detail": "Workspace is not connected",
                "next_action": "Run the mock closure gate after git is connected.",
            }
        )
        if item.id == "approval_git_tests"
        else item
        for item in report.milestones
    ]
    return report.model_copy(
        update={
            "ready": False,
            "status": "needs_attention",
            "score": 83,
            "ready_count": len(milestones) - 1,
            "milestones": milestones,
            "next_steps": ["Run the mock closure gate after git is connected."],
        }
    )


def _target_without_runtime() -> TargetReadinessResponse:
    report = _target_ready()
    next_actions = {
        "workspace_git": "Run the local runtime bootstrap with a git workspace.",
        "durable_event_store": "Use SQLite event-store mode before production.",
    }
    milestones = [
        item.model_copy(
            update={
                "ready": False,
                "status": "needs_attention",
                "detail": item.label + " is not production-ready",
                "next_action": next_actions[item.id],
            }
        )
        if item.id in next_actions
        else item
        for item in report.milestones
    ]
    return report.model_copy(
        update={
            "ready": False,
            "status": "needs_attention",
            "score": 71,
            "ready_count": len(milestones) - 2,
            "milestones": milestones,
            "next_steps": list(next_actions.values()),
        }
    )


def _evidence_ready() -> DemoEvidenceStatus:
    return DemoEvidenceStatus(
        path="/tmp/demo_evidence.json",
        exists=True,
        status="recorded",
        checked_at="2026-06-19T00:00:00+00:00",
        detail="3 records",
        records=[
            DemoEvidenceRecord(
                id="mock_e2e",
                label="Mock closure harness",
                status="passed",
                checked_at="2026-06-19T00:00:00+00:00",
                distinct_speaker_count=2,
                requested_chunks=3,
                completed_chunks=3,
                timeline_message_count=3,
                metrics={
                    "checks_passed": 7,
                    "agent_message_count": 1,
                    "closure_tests_passed": True,
                    "closure_branch": "voiceops/act-123-fix",
                    "agent_controls_cancelled": True,
                },
            )
        ],
    )


def _harnesses_ready():
    return {
        "meeting_closure": SimpleNamespace(
            action_id="act-123",
            external_assignment_id="asg-auto-123",
            external_action_id="act-auto-123",
            external_provider="local",
            external_auto_dispatched=True,
            external_pending_seen_by_bob=True,
            branch="voiceops/act-123-fix",
            tests_passed=True,
            pending_seen_by_bob=True,
            completed_seen_by_alice=True,
            handoff_lines=[
                "Priya Nair asked VoiceOps to patch app.py",
                "Approved by Sam Ortiz",
                "Branch: voiceops/act-123-fix",
            ],
        ),
        "multi_agent": SimpleNamespace(
            roles=["coordinator", "meeting", "memory", "code", "review", "code", "review", "test", "git"],
            exchange_count=8,
            revision_exchange_seen=True,
            revision_count=1,
            preapproval_tests_passed=True,
        ),
        "external_coding_agent": [
            SimpleNamespace(
                provider="claude",
                runtime_mode="local_cli_patch",
                runtime_workspace="temporary",
                branch="voiceops/act-345-claude",
                action_id="act-345",
                files_changed=["app.py"],
                tests_passed=True,
                pending_before_approval=True,
                workspace_unchanged_before_approval=True,
                room_workspace_isolated=True,
            ),
            SimpleNamespace(
                provider="codex",
                runtime_mode="local_cli_patch",
                runtime_workspace="temporary",
                branch="voiceops/act-456-codex",
                action_id="act-456",
                files_changed=["app.py"],
                tests_passed=True,
                pending_before_approval=True,
                workspace_unchanged_before_approval=True,
                room_workspace_isolated=True,
            ),
            SimpleNamespace(
                provider="local",
                runtime_mode="local_cli_patch",
                runtime_workspace="temporary",
                branch="voiceops/act-789-local",
                action_id="act-789",
                files_changed=["app.py"],
                tests_passed=True,
                pending_before_approval=True,
                workspace_unchanged_before_approval=True,
                room_workspace_isolated=True,
            ),
        ],
        "room_pull_request": SimpleNamespace(
            remote_url="https://github.com/room/app.git",
            workspace_source="room",
            ready=True,
            global_workspace_unchanged=True,
            web_url="https://github.com/room/app/pull/1",
        ),
        "rag": SimpleNamespace(
            provider="local_sparse",
            voice_citation_count=2,
            provenance_ontology_hits=1,
        ),
        "prompt_injection": SimpleNamespace(
            invariants_passed=[
                "workspace_secret_file_blocked",
                "workspace_secret_search_blocked",
                "mixed_code_change_route_requires_approval",
                "memory_rag_read_only",
                "voice_patch_preview_first",
                "viewer_cannot_approve",
                "room_snapshot_requires_membership",
                "project_restricted_room_access",
                "multi_agent_preview_first",
                "external_cli_env_sanitized",
                "external_cli_preview_first",
                "external_cli_status_claims_ignored",
                "auto_external_env_sanitized",
                "auto_external_preview_first",
                "auto_external_status_claims_ignored",
            ],
            blocked_attack_paths=["malicious_memory_executes_as_instruction"],
            viewer_approve_status=403,
            viewer_room_status=403,
            secret_search_hits=0,
            workspace_changed_before_approval=False,
            external_agent_secret_leaked=False,
            external_agent_claimed_approval_ignored=True,
            auto_external_action_id="act-auto-sec",
            auto_external_secret_leaked=False,
            auto_external_claimed_approval_ignored=True,
        ),
    }


def _preflight(check_id: str):
    return SimpleNamespace(id=check_id)
