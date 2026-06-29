from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = BACKEND_ROOT / "tests"
for path in (BACKEND_ROOT, TESTS_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.config import Settings
from app.system.evidence import DemoEvidenceRecord, read_demo_evidence
from app.system.router import TargetReadinessMilestone
from scripts.demo_operator import command_plan, profile_preflight
from scripts.target_readiness import run_target_readiness


@dataclass
class ProductAuditItem:
    id: str
    label: str
    status: str
    required: bool
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    command: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def failed_required(self) -> bool:
        return self.required and self.status != "passed"


def run_product_audit(*, run_harnesses: bool = False, settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    target = run_target_readiness(settings)
    milestones = {item.id: item for item in target.milestones}
    evidence_status = read_demo_evidence(settings.demo_evidence_path)
    evidence_records = {record.id: record for record in evidence_status.records}
    harnesses = _run_harnesses() if run_harnesses else {}

    items = [
        _target_item(
            "multi_speaker_live",
            "Multi-speaker live meeting",
            [milestones.get("real_multi_speaker"), milestones.get("real_live_backend"), milestones.get("real_browser_live")],
            command="cd backend && python scripts/demo_operator.py --profile real-mac --run --json",
        ),
        _target_item(
            "production_runtime",
            "Workspace git and durable event store",
            [milestones.get("workspace_git"), milestones.get("durable_event_store")],
            command="cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json",
        ),
        _mock_e2e_item(evidence_records.get("mock_e2e")),
        _memory_item(milestones.get("ai_memory_loop"), harnesses.get("rag")),
        _multi_agent_item(harnesses.get("multi_agent")),
        _external_coding_agent_item(harnesses.get("external_coding_agent")),
        _prompt_injection_item(harnesses.get("prompt_injection")),
        _approval_item(
            milestones.get("approval_git_tests"),
            evidence_records.get("mock_e2e"),
            harnesses.get("meeting_closure"),
            harnesses.get("room_pull_request"),
        ),
        _handoff_item(harnesses.get("meeting_closure")),
        _operator_item(),
    ]
    failed = [item for item in items if item.failed_required]
    return {
        "status": "ready" if not failed else "needs_attention",
        "ready": not failed,
        "run_harnesses": run_harnesses,
        "target_readiness": target.model_dump(mode="json"),
        "demo_evidence": {
            "status": evidence_status.status,
            "checked_at": evidence_status.checked_at,
            "record_ids": sorted(evidence_records),
        },
        "items": [asdict(item) for item in items],
        "next_steps": _next_steps(failed),
    }


def _target_item(
    item_id: str,
    label: str,
    milestones: list[TargetReadinessMilestone | None],
    *,
    command: str,
) -> ProductAuditItem:
    evidence: list[str] = []
    gaps: list[str] = []
    for milestone in milestones:
        if not milestone:
            gaps.append("Missing target readiness milestone.")
            continue
        detail = f"{milestone.label}: {milestone.status}"
        if milestone.evidence:
            detail += f" ({milestone.evidence})"
        evidence.append(detail)
        if not milestone.ready:
            gaps.append(milestone.next_action or milestone.detail)
    return ProductAuditItem(
        id=item_id,
        label=label,
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command=command,
    )


def _mock_e2e_item(record: DemoEvidenceRecord | None) -> ProductAuditItem:
    gaps: list[str] = []
    evidence: list[str] = []
    if not record or record.status != "passed":
        gaps.append("Run the browser E2E suite to record mock meeting closure evidence.")
    else:
        evidence.append(_record_summary(record))
        checks = int(record.metrics.get("checks_passed") or 0)
        if checks < 7:
            gaps.append(f"Browser E2E evidence should include all 7 mock checks, got {checks}.")
        if int(record.distinct_speaker_count or 0) < 2:
            gaps.append("Mock live evidence should include at least two speaker labels.")
        if not record.metrics.get("agent_controls_cancelled"):
            gaps.append("Agent controls cancellation evidence is missing.")
    return ProductAuditItem(
        id="browser_team_ui",
        label="Team UI, speaker mapping, and agent controls",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd frontend && npm run e2e:all -- --json",
    )


def _memory_item(milestone: TargetReadinessMilestone | None, rag_report: Any | None) -> ProductAuditItem:
    evidence: list[str] = []
    gaps: list[str] = []
    if milestone:
        evidence.append(f"{milestone.label}: {milestone.status} ({milestone.evidence or 'no evidence label'})")
        if not milestone.ready:
            gaps.append(milestone.next_action or milestone.detail)
    else:
        gaps.append("Missing AI memory target readiness milestone.")
    if rag_report is not None:
        evidence.append(
            f"RAG harness: provider={rag_report.provider}, citations={rag_report.voice_citation_count}, ontology={rag_report.provenance_ontology_hits}"
        )
        if rag_report.provider != "local_sparse":
            gaps.append(f"Expected local_sparse RAG provider, got {rag_report.provider}.")
        if rag_report.voice_citation_count < 1:
            gaps.append("Voice memory answer did not persist citations.")
        if rag_report.provenance_ontology_hits < 1:
            gaps.append("Provenance query did not hit ontology.")
    else:
        evidence.append("Deterministic RAG harness not run in this audit.")
    return ProductAuditItem(
        id="memory_rag",
        label="Short/long memory and cited RAG answers",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd backend && python scripts/product_audit.py --run-harnesses --json",
    )


def _multi_agent_item(report: Any | None) -> ProductAuditItem:
    evidence = []
    gaps = []
    if report is None:
        gaps.append("Run deterministic multi-agent harness for current-code proof.")
    else:
        evidence.append(
            f"roles={','.join(report.roles)} exchanges={report.exchange_count} revision={report.revision_exchange_seen}"
        )
        if not report.revision_exchange_seen:
            gaps.append("Review-to-code revision exchange was not recorded.")
        if report.revision_count < 1:
            gaps.append("Code agent did not revise after review.")
        if not report.preapproval_tests_passed:
            gaps.append("Pre-approval test validation failed.")
    return ProductAuditItem(
        id="multi_agent_review_loop",
        label="Specialist multi-agent review loop",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd backend && python scripts/smoke_multi_agent.py --json",
    )


def _external_coding_agent_item(report: Any | None) -> ProductAuditItem:
    evidence: list[str] = []
    gaps: list[str] = []
    if report is None:
        gaps.append("Run external coding agent harness for current-code proof.")
    else:
        reports = list(report) if isinstance(report, list | tuple) else [report]
        providers = {item.provider for item in reports}
        expected_providers = {"claude", "codex", "local"}
        missing = expected_providers - providers
        unexpected = providers - expected_providers
        if missing:
            gaps.append(f"Missing external coding agent harness providers: {', '.join(sorted(missing))}.")
        if unexpected:
            gaps.append(f"Unexpected external coding agent harness providers: {', '.join(sorted(unexpected))}.")
        for item in reports:
            _append_external_coding_agent_evidence(item, evidence, gaps)
    return ProductAuditItem(
        id="external_coding_agent_bridge",
        label="External coding agent bridge, local provider, and sandboxed approval",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command=(
            "cd backend && python scripts/smoke_external_coding_agent.py --provider claude --json "
            "&& python scripts/smoke_external_coding_agent.py --provider codex --json "
            "&& python scripts/smoke_external_coding_agent.py --provider local --json"
        ),
    )


def _append_external_coding_agent_evidence(report: Any, evidence: list[str], gaps: list[str]) -> None:
    evidence.append(
        f"provider={report.provider} runtime={report.runtime_mode}/{report.runtime_workspace} branch={report.branch} tests={report.tests_passed} room_workspace_isolated={getattr(report, 'room_workspace_isolated', False)}"
    )
    if report.runtime_mode != "local_cli_patch" or report.runtime_workspace != "temporary":
        gaps.append(f"{report.provider} did not run through temporary local CLI patch runtime.")
    if not report.pending_before_approval or not report.workspace_unchanged_before_approval:
        gaps.append(f"{report.provider} did not preserve preview-first workspace safety.")
    if not getattr(report, "room_workspace_isolated", False):
        gaps.append(f"{report.provider} did not prove room-scoped workspace isolation.")
    if not report.branch.startswith(f"voiceops/{report.action_id}-"):
        gaps.append(f"{report.provider} approved patch did not create the expected local branch.")
    if report.files_changed != ["app.py"]:
        gaps.append(f"{report.provider} produced unexpected external patch files: {report.files_changed}.")
    if not report.tests_passed:
        gaps.append(f"{report.provider} approved external coding agent patch tests did not pass.")


def _prompt_injection_item(report: Any | None) -> ProductAuditItem:
    evidence: list[str] = []
    gaps: list[str] = []
    if report is None:
        gaps.append("Run deterministic prompt-injection harness for current-code proof.")
    else:
        evidence.append(
            f"invariants={len(report.invariants_passed)} blocked_paths={len(report.blocked_attack_paths)} viewer_approve={report.viewer_approve_status} viewer_room={getattr(report, 'viewer_room_status', 'unknown')}"
        )
        if getattr(report, "auto_external_action_id", None):
            evidence.append(
                "auto external safety: "
                f"action={report.auto_external_action_id} "
                f"claimed_approval_ignored={getattr(report, 'auto_external_claimed_approval_ignored', False)}"
            )
        required = {
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
        }
        missing = sorted(required - set(report.invariants_passed))
        if missing:
            gaps.append(f"Prompt-injection harness missed invariants: {', '.join(missing)}.")
        if report.workspace_changed_before_approval:
            gaps.append("Workspace changed before approval under prompt-injection scenario.")
        if report.viewer_approve_status != 403:
            gaps.append(f"Viewer approval attempt returned {report.viewer_approve_status}, expected 403.")
        if getattr(report, "viewer_room_status", None) != 403:
            gaps.append(f"Unjoined viewer room snapshot returned {getattr(report, 'viewer_room_status', None)}, expected 403.")
        if report.secret_search_hits:
            gaps.append("Secret workspace file content appeared in search results.")
        if getattr(report, "auto_external_secret_leaked", True):
            gaps.append("Auto external-agent prompt-injection path leaked a service secret.")
        if not getattr(report, "auto_external_claimed_approval_ignored", False):
            gaps.append("Auto external-agent forged approval/status claims were not ignored.")
        if getattr(report, "external_agent_secret_leaked", True):
            gaps.append("External-agent prompt-injection path leaked a service secret.")
        if not getattr(report, "external_agent_claimed_approval_ignored", False):
            gaps.append("External-agent forged approval/status claims were not ignored.")
    return ProductAuditItem(
        id="prompt_injection_safety",
        label="Prompt-injection and tool-boundary safety",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd backend && python scripts/smoke_prompt_injection.py --json",
    )


def _approval_item(
    milestone: TargetReadinessMilestone | None,
    record: DemoEvidenceRecord | None,
    closure_report: Any | None,
    pr_report: Any | None = None,
) -> ProductAuditItem:
    evidence: list[str] = []
    gaps: list[str] = []
    closure_proves_approval = False
    if milestone:
        evidence.append(f"{milestone.label}: {milestone.status} ({milestone.evidence or 'no evidence label'})")
    else:
        gaps.append("Missing approval target readiness milestone.")
    if record:
        evidence.append(_record_summary(record))
        if not record.metrics.get("closure_tests_passed"):
            gaps.append("Browser closure evidence does not show passing tests.")
        if not record.metrics.get("closure_branch"):
            gaps.append("Browser closure evidence does not include created branch.")
    if closure_report is not None:
        evidence.append(
            f"closure harness: action={closure_report.action_id}, branch={closure_report.branch}, tests={closure_report.tests_passed}"
        )
        if getattr(closure_report, "external_assignment_id", None):
            evidence.append(
                "external auto-agent closure: "
                f"assignment={closure_report.external_assignment_id}, "
                f"action={closure_report.external_action_id}, "
                f"provider={closure_report.external_provider}, "
                f"auto_dispatched={closure_report.external_auto_dispatched}"
            )
        if not closure_report.pending_seen_by_bob or not closure_report.completed_seen_by_alice:
            gaps.append("Two-user visibility for pending/completed approval is incomplete.")
        if not getattr(closure_report, "external_auto_dispatched", False):
            gaps.append("Meeting closure harness did not prove voice-created external auto-agent dispatch.")
        if getattr(closure_report, "external_provider", None) != "local":
            gaps.append("Meeting closure harness did not prove the local/open external agent route.")
        if not getattr(closure_report, "external_pending_seen_by_bob", False):
            gaps.append("Bob did not see the voice-created external auto-agent pending approval.")
        if not closure_report.branch.startswith(f"voiceops/{closure_report.action_id}-"):
            gaps.append("Approved patch did not create the expected local branch.")
        if not closure_report.tests_passed:
            gaps.append("Approved patch tests did not pass.")
        closure_proves_approval = (
            closure_report.pending_seen_by_bob
            and closure_report.completed_seen_by_alice
            and closure_report.branch.startswith(f"voiceops/{closure_report.action_id}-")
            and bool(closure_report.tests_passed)
        )
    else:
        gaps.append("Run deterministic meeting closure harness for current-code proof.")
    if pr_report is not None:
        evidence.append(
            f"room PR dry-run: remote={pr_report.remote_url} source={pr_report.workspace_source} ready={pr_report.ready} global_unchanged={pr_report.global_workspace_unchanged}"
        )
        if not pr_report.ready:
            gaps.append("Room-scoped pull-request dry-run plan was not ready.")
        if pr_report.workspace_source != "room":
            gaps.append(f"Room-scoped pull-request plan reported workspace_source={pr_report.workspace_source!r}.")
        if not pr_report.global_workspace_unchanged:
            gaps.append("Room-scoped pull-request flow changed the global workspace.")
        if "github.com/room/app" not in pr_report.web_url:
            gaps.append(f"Room-scoped pull-request plan used unexpected web URL: {pr_report.web_url}.")
    elif closure_report is not None:
        gaps.append("Run room-scoped pull-request harness for current-code proof.")
    if milestone and not milestone.ready and not closure_proves_approval:
        gaps.append(milestone.next_action or milestone.detail)
    elif milestone and not milestone.ready and closure_proves_approval:
        evidence.append("Current-code closure harness proves approval workflow even though target readiness still needs local workspace configuration.")
    return ProductAuditItem(
        id="approval_git_tests",
        label="Approval-first patch, local branch, and tests",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd backend && python scripts/smoke_meeting_closure.py --json",
    )


def _handoff_item(closure_report: Any | None) -> ProductAuditItem:
    gaps: list[str] = []
    evidence: list[str] = []
    if closure_report is None:
        gaps.append("Run meeting closure harness to prove handoff content.")
    else:
        joined = "\n".join(closure_report.handoff_lines)
        evidence.extend(closure_report.handoff_lines[:5])
        for expected in ("Approved by Sam Ortiz", "Branch: voiceops/", "Priya Nair asked VoiceOps to patch"):
            if expected not in joined:
                gaps.append(f"Handoff missing {expected!r}.")
    return ProductAuditItem(
        id="handoff_audit",
        label="Returning teammate handoff and audit trail",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd backend && python scripts/smoke_meeting_closure.py --json",
    )


def _operator_item() -> ProductAuditItem:
    evidence: list[str] = []
    gaps: list[str] = []
    for profile in ("quick", "local", "real-mac"):
        commands = command_plan(profile)
        if not commands:
            gaps.append(f"{profile} operator profile has no commands.")
            continue
        evidence.append(f"{profile}: {', '.join(command.id for command in commands)}")
    real_preflight = profile_preflight("real-mac")
    if not real_preflight:
        gaps.append("real-mac profile should expose preflight checks.")
    else:
        evidence.append("real-mac preflight: " + ", ".join(item.id for item in real_preflight))
    return ProductAuditItem(
        id="operator_readiness",
        label="Operator command set and real-speaker preflight",
        status="passed" if not gaps else "failed",
        required=True,
        evidence=evidence,
        gaps=gaps,
        command="cd backend && python scripts/demo_operator.py --profile local --run --json",
    )


def _run_harnesses() -> dict[str, Any]:
    from external_coding_agent_harness import run_external_coding_agent_harness
    from meeting_closure_harness import run_meeting_closure_harness, run_pull_request_room_harness
    from multi_agent_harness import run_multi_agent_harness
    from prompt_injection_harness import run_prompt_injection_harness
    from rag_memory_harness import run_rag_memory_smoke

    return {
        "external_coding_agent": [
            run_external_coding_agent_harness(provider="claude"),
            run_external_coding_agent_harness(provider="codex"),
            run_external_coding_agent_harness(provider="local"),
        ],
        "meeting_closure": run_meeting_closure_harness(include_reject_path=True),
        "multi_agent": run_multi_agent_harness(),
        "prompt_injection": run_prompt_injection_harness(),
        "rag": run_rag_memory_smoke(),
        "room_pull_request": run_pull_request_room_harness(),
    }


def _record_summary(record: DemoEvidenceRecord) -> str:
    parts = [f"{record.id}: {record.status}"]
    if record.distinct_speaker_count:
        parts.append(f"{record.distinct_speaker_count} speakers")
    if record.completed_chunks:
        chunks = str(record.completed_chunks)
        if record.requested_chunks:
            chunks += f"/{record.requested_chunks}"
        parts.append(f"{chunks} chunks")
    return " | ".join(parts)


def _next_steps(failed: list[ProductAuditItem]) -> list[str]:
    steps: list[str] = []
    for item in failed:
        if item.command:
            steps.append(item.command)
        steps.extend(item.gaps)
    return list(dict.fromkeys(steps))[:8]


def print_text_report(report: dict) -> None:
    print(f"VoiceOps product audit: {report['status']}")
    for item in report["items"]:
        marker = "ok" if item["status"] == "passed" else "blocked"
        print(f"- {item['label']}: {marker}")
        for evidence in item.get("evidence") or []:
            print(f"  evidence: {evidence}")
        for gap in item.get("gaps") or []:
            print(f"  gap: {gap}")
        if item.get("command"):
            print(f"  command: {item['command']}")
    if report["next_steps"]:
        print("Next steps:")
        for step in report["next_steps"]:
            print(f"- {step}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit VoiceOps against the production AI teammate target.")
    parser.add_argument("--env-file", help="Read settings from a generated .env file instead of the current process environment.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable audit report.")
    parser.add_argument("--run-harnesses", action="store_true", help="Run deterministic backend harnesses before scoring.")
    parser.add_argument("--require-ready", action="store_true", help="Exit non-zero unless all required audit items pass.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(_env_file=args.env_file) if args.env_file else None
    report = (
        run_product_audit(run_harnesses=args.run_harnesses, settings=settings)
        if settings is not None
        else run_product_audit(run_harnesses=args.run_harnesses)
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    return 0 if report["ready"] or not args.require_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
