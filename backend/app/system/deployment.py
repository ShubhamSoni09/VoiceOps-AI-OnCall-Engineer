from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.collab.event_store_readiness import build_event_store_readiness
from app.config import Settings
from app.system.security import (
    SecurityControlCheck,
    StartupSecurityError,
    build_production_security_report,
    validate_startup_security,
)
from app.workspace.git import WorkspaceGitService
from app.workspace.models import GitStatusResponse
from app.workspace.tools import WorkspaceError


class DeploymentHardeningCheck(BaseModel):
    id: str
    label: str
    domain: str
    ready: bool
    status: str
    severity: str
    detail: str
    action: str | None = None
    command: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class DeploymentHardeningResponse(BaseModel):
    status: str
    ready: bool
    checked_at: str
    environment: str
    score: int
    ready_count: int
    total_count: int
    checks: list[DeploymentHardeningCheck]
    commands: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


def build_deployment_hardening_report(settings: Settings) -> DeploymentHardeningResponse:
    security = build_production_security_report(settings)
    security_checks = {check.id: check for check in security.checks}
    event_store = build_event_store_readiness(settings)
    git_status = _git_status(settings)
    checks = [
        _from_security(
            security_checks["jwt_secret"],
            action="Rotate JWT_SECRET with a generated 32+ character secret.",
            command="openssl rand -hex 32",
        ),
        _from_security(
            security_checks["default_users"],
            action="Disable SEED_DEMO_USERS and remove seeded demo accounts from the production user store.",
            command="python scripts/prepare_production_trial.py --output-dir ../voiceops-production-trial --frontend-origin https://voiceops.example.com --workspace /absolute/path/to/team/repository --admin-email admin@example.com --admin-name \"Team Admin\"",
        ),
        _bootstrap_secret_file_check(settings),
        _from_security(
            security_checks["cors_origins"],
            action="Set CORS_ALLOWED_ORIGINS to exact deployed frontend origins.",
        ),
        _from_security(
            security_checks["runtime_artifact_paths"],
            action="Move runtime artifacts to a private data directory outside VOICEOPS_WORKSPACE.",
        ),
        _event_store_check(event_store),
        _workspace_git_check(settings, git_status),
        _from_security(
            security_checks["speaker_provider"],
            action="Use SPEAKER_PROVIDER=whisperx and configure HF_TOKEN after accepting pyannote model terms.",
            command="python scripts/smoke_whisperx_provider.py --generate-macos-tts --record-status --require-multiple-speakers --json",
        ),
        _from_security(
            security_checks["external_agent_credentials"],
            action="Set EXTERNAL_AGENT_CREDENTIAL_SECRET distinct from JWT_SECRET before connecting provider tokens.",
        ),
        _from_security(
            security_checks["external_agent_execution_policy"],
            action=(
                "Restrict EXTERNAL_AGENT_ALLOWED_PROVIDERS to supported providers, "
                "keep production API base URLs HTTPS, and bound external-agent timeouts."
            ),
        ),
        _from_security(
            security_checks["github_side_effects"],
            action="Keep GITHUB_PR_CREATION_ENABLED=false until GitHub credentials, base branch allowlist, and audit policy are reviewed.",
        ),
        _startup_security_gate_check(settings),
        _operator_acceptance_check(),
    ]
    ready_count = sum(1 for check in checks if check.ready)
    total_count = len(checks)
    ready = all(check.ready for check in checks if check.severity in {"critical", "high"})
    next_steps = [check.action for check in checks if not check.ready and check.action]
    commands = [check.command for check in checks if not check.ready and check.command]
    commands.append("python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted")
    commands.append("python scripts/final_acceptance_audit.py --env-file ../voiceops-production-trial/.env --json --require-accepted")
    commands.append("python scripts/product_audit.py --env-file ../voiceops-production-trial/.env --run-harnesses --require-ready --json")
    return DeploymentHardeningResponse(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checked_at=datetime.now(UTC).isoformat(),
        environment=security.environment,
        score=round((ready_count / total_count) * 100) if total_count else 0,
        ready_count=ready_count,
        total_count=total_count,
        checks=checks,
        commands=list(dict.fromkeys(commands)),
        next_steps=list(dict.fromkeys(next_steps)),
    )


def _from_security(
    check: SecurityControlCheck,
    *,
    action: str | None = None,
    command: str | None = None,
) -> DeploymentHardeningCheck:
    return DeploymentHardeningCheck(
        id=check.id,
        label=check.label,
        domain=check.domain,
        ready=check.ready,
        status=check.status,
        severity=check.severity,
        detail=check.detail,
        action=action or (check.mitigations[0] if check.mitigations and not check.ready else None),
        command=command,
        evidence=check.evidence,
    )


def _bootstrap_secret_file_check(settings: Settings) -> DeploymentHardeningCheck:
    bootstrap_path = _bootstrap_path(settings.users_store_path)
    exists = bootstrap_path.exists()
    return DeploymentHardeningCheck(
        id="bootstrap_secret_file",
        label="Bootstrap admin password file is removed",
        domain="authentication",
        ready=not exists,
        status="ready" if not exists else "needs_attention",
        severity="critical",
        detail=(
            "No bootstrap-admin.txt file was found near the production user store."
            if not exists
            else f"Temporary bootstrap password file still exists at {bootstrap_path}."
        ),
        action="Rotate the bootstrap admin password, invite real users, then delete bootstrap-admin.txt.",
        evidence={"path": str(bootstrap_path), "exists": exists},
    )


def _bootstrap_path(users_store_path: Path) -> Path:
    path = users_store_path.expanduser()
    if path.parent.name == "data":
        return path.parent.parent / "bootstrap-admin.txt"
    return path.parent / "bootstrap-admin.txt"


def _event_store_check(event_store) -> DeploymentHardeningCheck:
    return DeploymentHardeningCheck(
        id="event_store_readiness",
        label="SQLite event store is initialized",
        domain="audit_logging",
        ready=event_store.ready,
        status=event_store.status,
        severity="high",
        detail=(
            f"{event_store.backend} event store has {event_store.event_count} events across {event_store.stream_count} streams."
            if event_store.ready
            else event_store.warnings[0] if event_store.warnings else "Event store is not ready."
        ),
        action="Initialize or migrate collaboration state to the SQLite event store.",
        command=event_store.migration_command,
        evidence={
            "backend": event_store.backend,
            "path": event_store.path,
            "event_count": event_store.event_count,
            "stream_count": event_store.stream_count,
        },
    )


def _workspace_git_check(settings: Settings, git_status: GitStatusResponse | None) -> DeploymentHardeningCheck:
    ready = bool(git_status and git_status.is_git_repo)
    detail = (
        f"{git_status.branch}, {len(git_status.files)} changed files"
        if ready and git_status
        else "VOICEOPS_WORKSPACE must point to a local git repository."
    )
    return DeploymentHardeningCheck(
        id="workspace_git",
        label="Editable workspace is a local git repository",
        domain="change_control",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=detail,
        action="Set VOICEOPS_WORKSPACE to the exact local git repository used for approved patches.",
        evidence={
            "workspace": settings.voiceops_workspace,
            "branch": git_status.branch if git_status else None,
            "dirty": git_status.dirty if git_status else None,
            "is_git_repo": git_status.is_git_repo if git_status else False,
        },
    )


def _operator_acceptance_check() -> DeploymentHardeningCheck:
    return DeploymentHardeningCheck(
        id="operator_acceptance_commands",
        label="Operator acceptance commands are available",
        domain="deployment",
        ready=True,
        status="ready",
        severity="medium",
        detail="Production-trial acceptance commands are packaged with the backend scripts.",
        command="python scripts/operator_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted",
        evidence={
            "scripts": [
                "operator_acceptance.py",
                "final_acceptance_audit.py",
                "product_audit.py",
                "security_readiness.py",
                "init_collab_event_store.py",
            ]
        },
    )


def _startup_security_gate_check(settings: Settings) -> DeploymentHardeningCheck:
    environment = settings.deployment_environment.strip().lower() or "local"
    gate_enabled = bool(settings.production_startup_security_gate)
    blockers: list[str] = []
    if environment != "production":
        blockers.append("DEPLOYMENT_ENVIRONMENT is not production.")
    if not gate_enabled:
        blockers.append("PRODUCTION_STARTUP_SECURITY_GATE is disabled.")
    try:
        validate_startup_security(settings)
        gate_passed = True
        startup_error = None
    except StartupSecurityError as exc:
        gate_passed = False
        startup_error = str(exc)
        blockers.append(startup_error)
    ready = environment == "production" and gate_enabled and gate_passed
    return DeploymentHardeningCheck(
        id="startup_security_gate",
        label="Production startup gate fails closed",
        domain="deployment",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="critical",
        detail=(
            "Startup security gate would allow this production configuration."
            if ready
            else blockers[0] if blockers else "Startup security gate is not enforcing production configuration."
        ),
        action=(
            None
            if ready
            else "Set DEPLOYMENT_ENVIRONMENT=production, keep PRODUCTION_STARTUP_SECURITY_GATE=true, and fix startup security blockers."
        ),
        command="python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted",
        evidence={
            "environment": environment,
            "production_startup_security_gate": gate_enabled,
            "startup_gate": "passed" if gate_passed else "blocked",
            "error": startup_error,
        },
    )


def _git_status(settings: Settings) -> GitStatusResponse | None:
    if not settings.voiceops_workspace:
        return None
    try:
        return WorkspaceGitService(settings).status()
    except (WorkspaceError, OSError):
        return None
