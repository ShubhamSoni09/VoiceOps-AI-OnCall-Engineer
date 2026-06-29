from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings


@dataclass(frozen=True)
class OnboardingCheck:
    id: str
    label: str
    ready: bool
    required: bool
    detail: str
    next_action: str | None = None

    @property
    def status(self) -> str:
        if self.ready:
            return "passed"
        return "failed" if self.required else "diagnostic"


@dataclass
class OnboardingReport:
    status: str
    ready: bool
    checks: list[OnboardingCheck]
    commands: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


ENV_REQUIRED_KEYS = [
    "DEPLOYMENT_ENVIRONMENT",
    "JWT_SECRET",
    "CORS_ALLOWED_ORIGINS",
    "USERS_STORE_PATH",
    "SEED_DEMO_USERS",
    "PRODUCTION_STARTUP_SECURITY_GATE",
    "COLLAB_STORE_BACKEND",
    "COLLAB_SQLITE_PATH",
    "SPEAKER_STORE_BACKEND",
    "SPEAKER_SQLITE_PATH",
    "AGENT_RUNS_PATH",
    "AGENT_LLM_ROUTES_PATH",
    "LONG_MEMORY_PATH",
    "RAG_INDEX_PATH",
    "LLM_CONNECTION_STORE_PATH",
    "EXTERNAL_AGENT_STORE_PATH",
    "DEMO_EVIDENCE_PATH",
    "VOICEOPS_WORKSPACE",
    "SPEAKER_PROVIDER",
    "HF_TOKEN",
    "WHISPERX_MODEL",
    "WHISPERX_DEVICE",
    "WHISPERX_WORKER_MODE",
    "LLM_PROVIDER",
    "STT_PROVIDER",
    "TTS_PROVIDER",
    "AGENT_DISPLAY_NAME",
    "GITHUB_PR_CREATION_ENABLED",
    "GITHUB_PR_ALLOWED_BASE_BRANCHES",
]

TEAM_DOC_PHRASES = [
    "Configure Environment",
    "prepare_production_trial.py",
    "Rotate Accounts",
    "Start Services",
    "Acceptance Flow",
    "Trial Script",
    "Failure Handling",
]

OPERATOR_DOC_PHRASES = [
    "operator_acceptance.py --json",
    "bootstrap_local_runtime.py",
    "prepare_production_trial.py",
    "bundle-manifest.json",
    "production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready",
    "production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted",
    "init_collab_event_store.py",
    "demo_operator.py --profile local --run --json",
    "product_audit.py --run-harnesses --require-ready --json",
    "product_audit.py --env-file ../voiceops-production-trial/.env --run-harnesses --require-ready --json",
    "security_readiness.py --env-file ../voiceops-production-trial/.env --require-ready --json",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check VoiceOps team onboarding package readiness.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    parser.add_argument("--require-ready", action="store_true", help="Exit non-zero unless required checks pass.")
    return parser


def run_team_onboarding_check(
    *,
    settings: Settings | None = None,
    project_root: Path | None = None,
) -> OnboardingReport:
    settings = settings or Settings()
    root = project_root or PROJECT_ROOT
    backend_root = root / "backend"
    docs_root = root / "docs"
    checks = [
        _file_contains(
            backend_root / ".env.production.example",
            ENV_REQUIRED_KEYS,
            "env_template",
            "Production environment template",
            "Add backend/.env.production.example with all required production-trial variables.",
        ),
        _file_contains(
            docs_root / "TEAM_ONBOARDING.md",
            TEAM_DOC_PHRASES,
            "team_onboarding_doc",
            "Team onboarding runbook",
            "Document environment setup, account rotation, startup, acceptance, and failure handling.",
        ),
        _file_contains(
            docs_root / "OPERATOR_RUNBOOK.md",
            OPERATOR_DOC_PHRASES,
            "operator_acceptance_flow",
            "Operator acceptance commands",
            "Keep docs/OPERATOR_RUNBOOK.md aligned with local, product audit, and security gates.",
        ),
        _path_exists(
            backend_root / "scripts" / "security_readiness.py",
            "security_gate_script",
            "Production security gate script",
            "Add backend/scripts/security_readiness.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "product_audit.py",
            "product_audit_script",
            "Product acceptance audit script",
            "Add backend/scripts/product_audit.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "demo_operator.py",
            "operator_script",
            "Operator command profile script",
            "Add backend/scripts/demo_operator.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "operator_acceptance.py",
            "operator_acceptance_script",
            "Single operator acceptance runner",
            "Add backend/scripts/operator_acceptance.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "bootstrap_local_runtime.py",
            "local_runtime_bootstrap_script",
            "Local runtime bootstrap script",
            "Add backend/scripts/bootstrap_local_runtime.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "prepare_production_trial.py",
            "production_trial_script",
            "Production trial preparation script",
            "Add backend/scripts/prepare_production_trial.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "init_collab_event_store.py",
            "event_store_init_script",
            "SQLite event-store initialization script",
            "Add backend/scripts/init_collab_event_store.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "production_trial_acceptance.py",
            "production_trial_acceptance_script",
            "One-command production trial acceptance script",
            "Add backend/scripts/production_trial_acceptance.py.",
        ),
        _path_exists(
            backend_root / "scripts" / "production_cutover_check.py",
            "production_cutover_script",
            "Final production cutover checklist script",
            "Add backend/scripts/production_cutover_check.py.",
        ),
        _settings_check(settings),
    ]
    blocking = [check for check in checks if check.required and not check.ready]
    return OnboardingReport(
        status="ready" if not blocking else "needs_attention",
        ready=not blocking,
        checks=checks,
        commands=[
            "cd backend && python scripts/team_onboarding_check.py --json --require-ready",
            "cd backend && python scripts/operator_acceptance.py --json --require-accepted",
            "cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json",
            "cd backend && python scripts/prepare_production_trial.py --output-dir ../voiceops-production-trial --frontend-origin https://voiceops.example.com --workspace /absolute/path/to/team/repository --admin-email admin@example.com --admin-name \"Team Admin\"",
            "cd backend && python scripts/production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready",
            "cd backend && python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted",
            "cd backend && python scripts/init_collab_event_store.py --sqlite ../voiceops-production-trial/data/collaboration.sqlite3 --report-json",
            "cd backend && python scripts/demo_operator.py --profile local --run --json",
            "cd backend && python scripts/product_audit.py --run-harnesses --require-ready --json",
            "cd backend && python scripts/product_audit.py --env-file ../voiceops-production-trial/.env --run-harnesses --require-ready --json",
            "cd backend && python scripts/security_readiness.py --env-file ../voiceops-production-trial/.env --require-ready --json",
        ],
        next_steps=[check.next_action for check in blocking if check.next_action],
    )


def _file_contains(
    path: Path,
    required_phrases: list[str],
    check_id: str,
    label: str,
    next_action: str,
) -> OnboardingCheck:
    if not path.exists():
        return OnboardingCheck(check_id, label, False, True, f"Missing {path}.", next_action)
    text = path.read_text(encoding="utf-8")
    missing = [phrase for phrase in required_phrases if phrase not in text]
    if missing:
        return OnboardingCheck(
            check_id,
            label,
            False,
            True,
            f"{path} is missing: {', '.join(missing)}.",
            next_action,
        )
    return OnboardingCheck(check_id, label, True, True, f"{path} contains required onboarding content.")


def _path_exists(path: Path, check_id: str, label: str, next_action: str) -> OnboardingCheck:
    return OnboardingCheck(
        check_id,
        label,
        path.exists(),
        True,
        f"{path} exists." if path.exists() else f"Missing {path}.",
        None if path.exists() else next_action,
    )


def _settings_check(settings: Settings) -> OnboardingCheck:
    if settings.deployment_environment == "production":
        return OnboardingCheck(
            "current_settings_environment",
            "Current settings target production",
            True,
            False,
            "Current environment is production; run security_readiness.py --require-ready before inviting a team.",
        )
    return OnboardingCheck(
        "current_settings_environment",
        "Current settings target local development",
        True,
        False,
        "Current environment is local; package checks can pass without production secrets.",
    )


def print_text_report(report: OnboardingReport) -> None:
    print(f"VoiceOps team onboarding readiness: {report.status}")
    for check in report.checks:
        marker = "ok" if check.ready else ("blocked" if check.required else "note")
        print(f"- {check.label}: {marker}")
        print(f"  {check.detail}")
        if check.next_action and not check.ready:
            print(f"  next: {check.next_action}")
    print("Acceptance commands:")
    for command in report.commands:
        print(f"- {command}")


def report_to_dict(report: OnboardingReport) -> dict:
    data = asdict(report)
    data["checks"] = [
        {
            **asdict(check),
            "status": check.status,
        }
        for check in report.checks
    ]
    return data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_team_onboarding_check()
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print_text_report(report)
    if args.require_ready and not report.ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
