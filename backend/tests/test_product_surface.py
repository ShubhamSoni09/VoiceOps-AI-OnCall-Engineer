from __future__ import annotations

import json
from pathlib import Path

from app.main import app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


def test_product_critical_api_routes_are_mounted():
    paths = {getattr(route, "path", "") for route in app.routes}

    required_paths = {
        "/agents/rooms/{room_id}/assignments/{assignment_id}/dispatch",
        "/auth/users",
        "/auth/users/{user_id}/projects",
        "/collab/rooms/{room_id}/actions/{action_id}/approve",
        "/collab/rooms/{room_id}/actions/{action_id}/reject",
        "/collab/rooms/{room_id}/commands/route",
        "/collab/rooms/{room_id}/handoff",
        "/collab/rooms/{room_id}/memory/query",
        "/collab/rooms/{room_id}/rag/query",
        "/collab/rooms/{room_id}/work-dashboard",
        "/collab/rooms/{room_id}/workspace/clone",
        "/external-agents/providers",
        "/external-agents/providers/{provider}/credentials/local-cli",
        "/llm/providers",
        "/llm/providers/{provider}/credential",
        "/llm/providers/{provider}/credentials/api-key",
        "/llm/providers/{provider}/preflight",
        "/memory/rooms/{room_id}/long/query",
        "/ontology/rooms/{room_id}/query",
        "/speakers/rooms/{room_id}/live",
        "/speakers/rooms/{room_id}/mappings",
        "/speakers/rooms/{room_id}/segments",
        "/system/observability",
        "/system/deployment/cutover",
        "/system/deployment/hardening",
        "/system/security/readiness",
        "/system/target-readiness",
        "/voice/process-text",
        "/workspace/git/diff",
        "/workspace/git/status",
        "/workspace/readiness",
    }

    assert required_paths <= paths


def test_operator_and_acceptance_scripts_are_packaged():
    required_scripts = {
        "bootstrap_local_runtime.py",
        "final_acceptance_audit.py",
        "prepare_production_trial.py",
        "production_cutover_check.py",
        "product_audit.py",
        "production_trial_acceptance.py",
        "security_readiness.py",
        "source_tree_hygiene.py",
        "smoke_external_coding_agent.py",
        "smoke_meeting_closure.py",
        "smoke_multi_agent.py",
        "smoke_prompt_injection.py",
        "smoke_rag_memory.py",
        "team_onboarding_check.py",
    }

    existing = {path.name for path in (BACKEND_ROOT / "scripts").glob("*.py")}
    assert required_scripts <= existing


def test_frontend_package_exposes_product_e2e_gates():
    package = json.loads((FRONTEND_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    expected_scripts = {
        "e2e:all": "tests/e2e/runAll.e2e.mjs",
        "e2e:agent-controls": "tests/e2e/agentControls.e2e.mjs",
        "e2e:external-patch": "tests/e2e/externalAgentPatchClosure.e2e.mjs",
        "e2e:generated-speaker": "tests/e2e/generatedSpeakerVerification.e2e.mjs",
        "e2e:live-mock-mic": "tests/e2e/liveMeetingMockMic.e2e.mjs",
        "e2e:live-smoke": "tests/e2e/liveMeetingSmoke.e2e.mjs",
        "e2e:meeting-closure": "tests/e2e/meetingClosure.e2e.mjs",
        "e2e:meeting-memory": "tests/e2e/meetingMemory.e2e.mjs",
        "e2e:speaker-calibration": "tests/e2e/speakerCalibration.e2e.mjs",
    }

    for script_name, e2e_path in expected_scripts.items():
        assert script_name in scripts
        assert e2e_path in scripts[script_name]
        assert (FRONTEND_ROOT / e2e_path).exists()
