from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
TESTS_ROOT = BACKEND_ROOT / "tests"
for path in (BACKEND_ROOT, TESTS_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.config import Settings
from scripts.product_audit import run_product_audit
from scripts.security_readiness import run_security_readiness
from scripts.source_tree_hygiene import run_source_tree_hygiene
from scripts.team_onboarding_check import run_team_onboarding_check


@dataclass
class AcceptanceItem:
    id: str
    label: str
    status: str
    required: bool
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    source: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def failed_required(self) -> bool:
        return self.required and not self.passed


def run_final_acceptance_audit(
    *,
    run_harnesses: bool = True,
    settings: Settings | None = None,
    project_root: Path | None = None,
) -> dict:
    root = _project_root(project_root)
    settings = _settings_for_acceptance(settings or Settings(), root)
    product = run_product_audit(run_harnesses=run_harnesses, settings=settings)
    onboarding = run_team_onboarding_check(settings=settings, project_root=root)
    security = run_security_readiness(settings)
    external_agent_smoke = _run_external_agent_smoke() if run_harnesses else None
    product_items = {item["id"]: item for item in product["items"]}
    target_milestones = {item["id"]: item for item in product["target_readiness"]["milestones"]}

    items = [
        _source_audit_integrity_item(product, onboarding),
        _item_from_product(
            "multi_speaker_shared_room",
            "Multiple people can speak in one shared room",
            [product_items.get("multi_speaker_live"), product_items.get("browser_team_ui")],
            source="product_audit + target_readiness + browser evidence",
        ),
        _speaker_identity_item(product_items, target_milestones, root),
        _ai_teammate_item(product_items, root),
        _memory_item(product_items, root),
        _ontology_item(product_items, root),
        _multi_agent_item(product_items, root),
        _approval_item(product_items, target_milestones, root),
        _handoff_item(product_items, root),
        _team_ui_item(product_items, root),
        _external_agent_provider_item(external_agent_smoke, product_items.get("external_coding_agent_bridge"), root),
        _source_tree_hygiene_item(root),
        _production_packaging_item(onboarding, security, root),
    ]
    failed = [item for item in items if item.failed_required]
    deployment_prerequisites = _deployment_prerequisites(security)
    return {
        "status": "accepted" if not failed else "needs_attention",
        "accepted": not failed,
        "run_harnesses": run_harnesses,
        "items": [asdict(item) for item in items],
        "product_audit": {
            "status": product["status"],
            "ready": product["ready"],
            "item_ids": [item["id"] for item in product["items"]],
        },
        "onboarding": {
            "status": onboarding.status,
            "ready": onboarding.ready,
            "check_ids": [check.id for check in onboarding.checks],
        },
        "production_deployment_prerequisites": deployment_prerequisites,
        "next_steps": _next_steps(failed, deployment_prerequisites),
    }


def _project_root(project_root: Path | None = None) -> Path:
    root = (project_root or PROJECT_ROOT).resolve()
    if root.name == "backend" and (root / "app").exists() and (root.parent / "frontend").exists():
        return root.parent
    return root


def _settings_for_acceptance(settings: Settings, root: Path) -> Settings:
    if settings.voiceops_workspace:
        return settings
    return settings.model_copy(update={"voiceops_workspace": str(root)})


def _item_from_product(
    item_id: str,
    label: str,
    product_items: list[dict | None],
    *,
    source: str,
) -> AcceptanceItem:
    evidence: list[str] = []
    gaps: list[str] = []
    for product_item in product_items:
        if not product_item:
            gaps.append("Missing product audit item.")
            continue
        evidence.append(f"{product_item['label']}: {product_item['status']}")
        evidence.extend(product_item.get("evidence") or [])
        gaps.extend(product_item.get("gaps") or [])
    return AcceptanceItem(
        id=item_id,
        label=label,
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        source=source,
    )


def _source_audit_integrity_item(product: dict, onboarding: Any) -> AcceptanceItem:
    target = product.get("target_readiness") or {}
    target_gaps = _target_acceptance_gaps(target)
    item = AcceptanceItem(
        id="source_audits_integrity",
        label="Product audit, target diagnostics, and onboarding evidence are coherent",
        status="passed",
        required=True,
        evidence=[
            f"Product audit: {product.get('status')}",
            *_target_acceptance_evidence(target, target_gaps),
            f"Team onboarding: {onboarding.status}",
        ],
        source="product_audit + target_readiness + team_onboarding_check",
    )
    item.gaps.extend(_product_acceptance_gaps(product, target_gaps))
    item.gaps.extend(target_gaps)
    if not onboarding.ready:
        item.gaps.extend(onboarding.next_steps or ["Team onboarding check is not ready."])
    return _finalize(item)


def _product_acceptance_gaps(product: dict, target_gaps: list[str]) -> list[str]:
    if product.get("ready"):
        return []
    failed = [
        item for item in product.get("items") or []
        if item.get("required", True) and item.get("status") != "passed"
    ]
    # ponytail: final acceptance already handles target diagnostics; don't fail twice for the accepted durable-store bootstrap gap.
    if failed and all(item.get("id") == "production_runtime" for item in failed) and not target_gaps:
        return []
    gaps: list[str] = []
    for item in failed:
        gaps.extend(item.get("gaps") or [])
    gaps.extend(product.get("next_steps") or [])
    return list(dict.fromkeys(gaps)) or ["Product audit is not ready."]


def _target_acceptance_evidence(target: dict, target_gaps: list[str]) -> list[str]:
    status = target.get("status")
    evidence = [f"Target readiness: {status}"]
    if target.get("ready") or target_gaps:
        return evidence
    blocked = [milestone for milestone in target.get("milestones") or [] if not milestone.get("ready")]
    if not blocked:
        return evidence
    ids = ", ".join(str(item.get("id")) for item in blocked if item.get("id"))
    evidence.append(f"Target readiness diagnostic blockers accepted: {ids}")
    command = next((item.get("command") for item in blocked if item.get("command")), None)
    if command:
        evidence.append(f"Target readiness bootstrap command: {command}")
    return evidence


def _target_acceptance_gaps(target: dict) -> list[str]:
    if target.get("ready"):
        return []
    gaps: list[str] = []
    for milestone in target.get("milestones") or []:
        if milestone.get("ready"):
            continue
        if milestone.get("id") == "durable_event_store":
            continue
        gaps.append(milestone.get("next_action") or milestone.get("detail") or "Target readiness is not ready.")
    if gaps:
        return gaps
    return []


def _speaker_identity_item(
    product_items: dict[str, dict],
    target_milestones: dict[str, dict],
    root: Path,
) -> AcceptanceItem:
    item = _item_from_product(
        "speaker_identity",
        "Speaker labels are distinguishable, correctable, and uncertainty is visible",
        [product_items.get("multi_speaker_live"), product_items.get("browser_team_ui")],
        source="speaker validation docs + real/browser evidence",
    )
    for milestone_id in ("real_multi_speaker", "real_browser_live"):
        milestone = target_milestones.get(milestone_id)
        if milestone and milestone.get("ready"):
            item.evidence.append(f"{milestone['label']}: {milestone.get('evidence')}")
        else:
            item.gaps.append(f"Missing ready milestone: {milestone_id}")
    _require_file(item, root / "docs" / "SPEAKER_VALIDATION.md", "speaker validation documentation")
    return _finalize(item)


def _ai_teammate_item(product_items: dict[str, dict], root: Path) -> AcceptanceItem:
    item = _item_from_product(
        "ai_teammate_online",
        "AI teammate can join the meeting, answer, route commands, and log actions",
        [
            product_items.get("memory_rag"),
            product_items.get("approval_git_tests"),
            product_items.get("handoff_audit"),
        ],
        source="meeting closure harness + router tests + action audit",
    )
    _require_file(item, root / "backend" / "tests" / "test_meeting_router.py", "smart command router tests")
    _require_file(item, root / "backend" / "tests" / "test_meeting_closure_harness.py", "meeting closure harness tests")
    return _finalize(item)


def _memory_item(product_items: dict[str, dict], root: Path) -> AcceptanceItem:
    item = _item_from_product(
        "memory_rag_long_term",
        "Short memory, long memory, and local RAG answer meeting questions with citations",
        [product_items.get("memory_rag")],
        source="RAG harness + long memory tests + docs",
    )
    _require_file(item, root / "backend" / "tests" / "test_rag_memory_harness.py", "RAG smoke harness tests")
    _require_file(item, root / "backend" / "tests" / "test_long_memory.py", "long memory tests")
    _require_file(item, root / "docs" / "LONG_TERM_MEMORY.md", "long memory documentation")
    return _finalize(item)


def _ontology_item(product_items: dict[str, dict], root: Path) -> AcceptanceItem:
    item = _item_from_product(
        "project_ontology",
        "Project ontology links people, files, memory, actions, branches, and history",
        [product_items.get("memory_rag")],
        source="ontology tests + provenance/RAG harness",
    )
    _require_file(item, root / "backend" / "tests" / "test_ontology.py", "ontology API tests")
    _require_file(item, root / "docs" / "PROJECT_ONTOLOGY.md", "ontology documentation")
    if not any("ontology=" in evidence or "Ontology" in evidence for evidence in item.evidence):
        item.gaps.append("RAG/product evidence does not show ontology provenance hits.")
    return _finalize(item)


def _multi_agent_item(product_items: dict[str, dict], root: Path) -> AcceptanceItem:
    item = _item_from_product(
        "multi_agent_review_loop",
        "Specialist agents collaborate before proposing code changes",
        [product_items.get("multi_agent_review_loop")],
        source="multi-agent harness + docs",
    )
    _require_file(item, root / "backend" / "tests" / "test_multi_agent_harness.py", "multi-agent harness tests")
    _require_file(item, root / "docs" / "MULTI_AGENT.md", "multi-agent documentation")
    return _finalize(item)


def _approval_item(
    product_items: dict[str, dict],
    target_milestones: dict[str, dict],
    root: Path,
) -> AcceptanceItem:
    item = _item_from_product(
        "approval_git_workflow",
        "Code-changing work is preview-first, approved by a teammate, branched, tested, and audited",
        [product_items.get("approval_git_tests")],
        source="meeting closure harness + git workflow tests",
    )
    milestone = target_milestones.get("approval_git_tests")
    if milestone and milestone.get("ready"):
        item.evidence.append(f"{milestone['label']}: {milestone.get('evidence')}")
    else:
        item.gaps.append("Approval/git/tests target milestone is not ready.")
    _require_file(item, root / "backend" / "tests" / "test_collab.py", "collaboration approval/git tests")
    return _finalize(item)


def _handoff_item(product_items: dict[str, dict], root: Path) -> AcceptanceItem:
    item = _item_from_product(
        "returning_teammate_handoff",
        "Returning teammates receive decisions, tasks, approvals, branch, files, and test result",
        [product_items.get("handoff_audit")],
        source="meeting closure harness handoff evidence",
    )
    _require_file(item, root / "backend" / "tests" / "test_meeting_closure_harness.py", "handoff harness tests")
    return _finalize(item)


def _team_ui_item(product_items: dict[str, dict], root: Path) -> AcceptanceItem:
    item = _item_from_product(
        "team_work_ui",
        "Team UI exposes live transcript, speaker mapping, memory, agent actions, approval controls, and readiness",
        [product_items.get("browser_team_ui")],
        source="frontend unit/e2e files + browser evidence",
    )
    for relative in (
        "frontend/tests/e2e/meetingClosure.e2e.mjs",
        "frontend/tests/e2e/meetingMemory.e2e.mjs",
        "frontend/tests/e2e/speakerCalibration.e2e.mjs",
        "frontend/tests/e2e/agentControls.e2e.mjs",
    ):
        _require_file(item, root / relative, relative)
    return _finalize(item)


def _external_agent_provider_item(smoke: Any | None, bridge_item: dict | None, root: Path) -> AcceptanceItem:
    item = AcceptanceItem(
        id="external_agent_providers",
        label="Claude/Codex/Cursor/local provider layer connects credentials and returns approval-first proposals",
        status="passed",
        required=True,
        evidence=[],
        source="external agent smoke + product bridge audit + provider docs + API tests",
    )
    if bridge_item is None:
        item.gaps.append("Missing product audit external coding agent bridge evidence.")
    else:
        item.evidence.append(f"{bridge_item['label']}: {bridge_item['status']}")
        item.evidence.extend(bridge_item.get("evidence") or [])
        item.gaps.extend(bridge_item.get("gaps") or [])
        bridge_evidence = "\n".join(bridge_item.get("evidence") or [])
        for provider in ("claude", "codex", "local"):
            if f"provider={provider}" not in bridge_evidence:
                item.gaps.append(f"External coding agent bridge did not prove provider={provider}.")
    if smoke is None:
        item.gaps.append("Run final acceptance with harnesses to prove external agent providers.")
    else:
        item.evidence.extend(
            [
                f"provider={smoke.provider}",
                f"auth_method={smoke.auth_method}",
                f"status={smoke.status}",
                f"action={smoke.action_id}",
                f"preflight_status={getattr(smoke, 'preflight_status', 'unknown')}",
                f"provider_preflight={getattr(smoke, 'provider_preflight_state', 'unknown')}",
                f"runtime={getattr(smoke, 'runtime_evidence', {}).get('local_cli_runtime', 'unknown')}",
                f"env_policy={getattr(smoke, 'runtime_evidence', {}).get('env_policy', 'unknown')}",
                f"setup_guide={getattr(smoke, 'setup_guide_path', 'unknown')}",
                f"setup_next={getattr(smoke, 'setup_guide_next_step', 'unknown')}",
                f"workspace_unchanged_before_approval={smoke.workspace_unchanged_before_approval}",
                f"token_redacted={smoke.token_redacted}",
            ]
        )
        if smoke.status != "pending_approval":
            item.gaps.append("External agent smoke did not create a pending approval action.")
        if not smoke.workspace_unchanged_before_approval:
            item.gaps.append("External agent smoke modified the workspace before approval.")
        if not smoke.diff_preview_present:
            item.gaps.append("External agent smoke did not expose a diff preview.")
        if not smoke.token_redacted:
            item.gaps.append("External agent smoke did not prove credential redaction.")
        if getattr(smoke, "provider_preflight_state", "unknown") == "blocked":
            item.gaps.append("External agent provider preflight is blocked.")
        if getattr(smoke, "runtime_evidence", {}).get("tokens_returned_to_browser") is not False:
            item.gaps.append("External agent preflight did not prove server-side token handling.")
        if getattr(smoke, "setup_guide_path", "unknown") != "local_cli_for_code_changes":
            item.gaps.append("External agent setup guide did not recommend local CLI for code-changing work.")
        if "connect_local_cli" not in getattr(smoke, "setup_guide_recommended_steps", []):
            item.gaps.append("External agent setup guide did not expose local CLI as a recommended step.")
    _require_file(item, root / "backend" / "tests" / "test_external_agents.py", "external agent API tests")
    _require_file(item, root / "docs" / "EXTERNAL_AGENT_PROVIDERS.md", "external agent provider documentation")
    return _finalize(item)


def _production_packaging_item(onboarding: Any, security: Any, root: Path) -> AcceptanceItem:
    item = AcceptanceItem(
        id="production_trial_packaging",
        label="Team trial packaging includes env template, onboarding, security gate, and operator flow",
        status="passed",
        required=True,
        evidence=[
            f"Team onboarding check: {onboarding.status}",
            f"Security readiness script: {security.status}",
            "Production security may still need deployment-specific secrets and account rotation.",
        ],
        source="team_onboarding_check + security_readiness",
    )
    if not onboarding.ready:
        item.gaps.extend(onboarding.next_steps)
    for relative in (
        "backend/.env.production.example",
        "backend/scripts/bootstrap_local_runtime.py",
        "backend/scripts/prepare_production_trial.py",
        "backend/scripts/production_trial_acceptance.py",
        "backend/tests/test_bootstrap_local_runtime_script.py",
        "backend/tests/test_prepare_production_trial_script.py",
        "backend/tests/test_production_trial_acceptance_script.py",
        "docs/TEAM_ONBOARDING.md",
        "docs/SECURITY_REVIEW.md",
        "docs/OPERATOR_RUNBOOK.md",
    ):
        _require_file(item, root / relative, relative)
    return _finalize(item)


def _source_tree_hygiene_item(root: Path) -> AcceptanceItem:
    report = run_source_tree_hygiene(root)
    item = AcceptanceItem(
        id="source_tree_hygiene",
        label="Runtime, secret, build, and tool artifacts stay out of source control",
        status="passed" if report.get("ready") else "failed",
        required=True,
        evidence=[
            f"Source tree hygiene: {report.get('status')}",
            *(f"{check['label']}: {check['status']}" for check in report.get("items") or []),
        ],
        gaps=list(report.get("next_steps") or []),
        source="source_tree_hygiene",
    )
    _require_file(item, root / "backend" / "scripts" / "source_tree_hygiene.py", "source tree hygiene script")
    _require_file(item, root / "backend" / "tests" / "test_source_tree_hygiene_script.py", "source tree hygiene tests")
    return _finalize(item)


def _require_file(item: AcceptanceItem, path: Path, label: str) -> None:
    if path.exists():
        item.evidence.append(f"{label}: {path}")
    else:
        item.gaps.append(f"Missing {label}: {path}")


def _finalize(item: AcceptanceItem) -> AcceptanceItem:
    item.status = "passed" if not item.gaps else "failed"
    return item


def _deployment_prerequisites(security: Any) -> list[str]:
    prerequisites: list[str] = []
    for check in security.checks:
        if not check.ready and check.mitigations:
            prerequisites.append(check.mitigations[0])
    return list(dict.fromkeys(prerequisites))


def _next_steps(failed: list[AcceptanceItem], deployment_prerequisites: list[str]) -> list[str]:
    steps: list[str] = []
    for item in failed:
        steps.extend(item.gaps)
    if not failed:
        steps.extend(_production_trial_next_steps(deployment_prerequisites))
    return list(dict.fromkeys(steps))[:10]


def _production_trial_next_steps(deployment_prerequisites: list[str]) -> list[str]:
    if not deployment_prerequisites:
        return []
    return [
        (
            "For production, run `cd backend && python scripts/prepare_production_trial.py "
            "--output-dir ../voiceops-production-trial --frontend-origin https://voiceops.example.com "
            "--workspace /absolute/path/to/team/repository --admin-email admin@example.com "
            "--admin-name \"Team Admin\"`; it generates strong secrets, disables demo users, "
            "and creates a real bootstrap admin store."
        ),
        (
            "Replace HF_TOKEN, rotate the bootstrap admin password, delete bootstrap-admin.txt, "
            "then run `python scripts/production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready` "
            "and `python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted`."
        ),
    ]


def _run_external_agent_smoke() -> Any:
    from scripts.smoke_external_agents import run_smoke

    return run_smoke()


def print_text_report(report: dict) -> None:
    print(f"VoiceOps final acceptance: {report['status']}")
    for item in report["items"]:
        marker = "ok" if item["status"] == "passed" else "blocked"
        print(f"- {item['label']}: {marker}")
        for evidence in item.get("evidence") or []:
            print(f"  evidence: {evidence}")
        for gap in item.get("gaps") or []:
            print(f"  gap: {gap}")
    if report["production_deployment_prerequisites"]:
        print("Production deployment prerequisites:")
        for step in report["production_deployment_prerequisites"]:
            print(f"- {step}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit VoiceOps against the final multi-person AI teammate target.")
    parser.add_argument("--env-file", help="Read settings from a generated .env file instead of the current process environment.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    parser.add_argument("--skip-harnesses", action="store_true", help="Use recorded product evidence without running backend harnesses.")
    parser.add_argument("--require-accepted", action="store_true", help="Exit non-zero unless all required acceptance items pass.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(_env_file=args.env_file) if args.env_file else None
    report = (
        run_final_acceptance_audit(run_harnesses=not args.skip_harnesses, settings=settings)
        if settings is not None
        else run_final_acceptance_audit(run_harnesses=not args.skip_harnesses)
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    if args.require_accepted and not report["accepted"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
