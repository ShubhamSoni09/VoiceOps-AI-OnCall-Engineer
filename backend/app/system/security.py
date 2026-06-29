from __future__ import annotations

import json
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.auth.users import DEFAULT_USERS
from app.config import Settings

DEFAULT_JWT_SECRET = "change-me-in-production-voiceops"
DEFAULT_USER_EMAILS = {item["email"].lower() for item in DEFAULT_USERS}
SUPPORTED_EXTERNAL_AGENT_PROVIDERS = {"claude", "codex", "cursor", "local"}
PLACEHOLDER_SECRET_MARKERS = ("replace-with", "change-me", "example.com")


class SecurityThreat(BaseModel):
    id: str
    category: str
    title: str
    target: str
    risk_level: str
    controls: list[str] = Field(default_factory=list)


class SecurityRequirement(BaseModel):
    id: str
    title: str
    domain: str
    priority: str
    threat_refs: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)


class SecurityControlCheck(BaseModel):
    id: str
    label: str
    domain: str
    ready: bool
    status: str
    severity: str
    detail: str
    threats: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ProductionSecurityReport(BaseModel):
    status: str
    ready: bool
    checked_at: str
    environment: str
    checks: list[SecurityControlCheck]
    threats: list[SecurityThreat]
    requirements: list[SecurityRequirement]
    next_steps: list[str] = Field(default_factory=list)


class StartupSecurityError(RuntimeError):
    pass


def validate_startup_security(settings: Settings) -> None:
    environment = settings.deployment_environment.strip().lower() or "local"
    if environment != "production" or not settings.production_startup_security_gate:
        return
    report = build_production_security_report(settings)
    blockers = [
        check
        for check in report.checks
        if not check.ready and check.severity in {"critical", "high"}
    ]
    if not blockers:
        return
    detail = "; ".join(f"{check.id}: {check.detail}" for check in blockers)
    raise StartupSecurityError(f"Production security gate failed: {detail}")


def build_production_security_report(settings: Settings) -> ProductionSecurityReport:
    environment = settings.deployment_environment.strip().lower() or "local"
    production = environment == "production"
    threats = _threats()
    requirements = _requirements()
    checks = [
        _jwt_secret_check(settings, production),
        _cors_check(settings, production),
        _default_users_check(settings, production),
        _store_backend_check(
            "collab_store",
            "Collaboration store uses production backend",
            settings.collab_store_backend,
            production,
            threats=["T-02", "R-01", "I-02"],
            requirements=["SR-STORE-01", "SR-AUDIT-01"],
        ),
        _store_backend_check(
            "speaker_store",
            "Speaker identity store uses production backend",
            settings.speaker_store_backend,
            production,
            threats=["T-02", "I-02"],
            requirements=["SR-STORE-01", "SR-DATA-01"],
        ),
        _workspace_check(settings, production),
        _runtime_artifact_paths_check(settings, production),
        _speaker_provider_check(settings, production),
        _external_agent_credentials_check(settings, production),
        _external_agent_execution_policy_check(settings, production),
        _github_side_effect_check(settings),
    ]
    required_ready = all(check.ready for check in checks if production or check.severity in {"critical", "high"})
    next_steps = [check.mitigations[0] for check in checks if not check.ready and check.mitigations]
    return ProductionSecurityReport(
        status="ready" if required_ready else "needs_attention",
        ready=required_ready,
        checked_at=datetime.now(UTC).isoformat(),
        environment=environment,
        checks=checks,
        threats=threats,
        requirements=requirements,
        next_steps=next_steps,
    )


def _jwt_secret_check(settings: Settings, production: bool) -> SecurityControlCheck:
    secret = settings.jwt_secret or ""
    ready = _configured_secret(secret) and secret != DEFAULT_JWT_SECRET and len(secret) >= 32
    if not production and settings.jwt_secret == DEFAULT_JWT_SECRET:
        ready = False
    return SecurityControlCheck(
        id="jwt_secret",
        label="JWT secret is non-default and strong",
        domain="authentication",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="critical",
        detail="JWT secret is configured with a non-default value." if ready else "JWT secret is default or shorter than 32 characters.",
        threats=["S-01", "E-01"],
        requirements=["SR-AUTH-01"],
        mitigations=["Set JWT_SECRET to a generated 32+ character secret in production secret storage."],
        evidence={"length": len(secret), "default": settings.jwt_secret == DEFAULT_JWT_SECRET},
    )


def _cors_check(settings: Settings, production: bool) -> SecurityControlCheck:
    origins = [str(item).strip() for item in settings.cors_allowed_origins if str(item).strip()]
    wildcard = "*" in origins
    ready = bool(origins) and not (production and wildcard)
    return SecurityControlCheck(
        id="cors_origins",
        label="CORS origins are explicit for production",
        domain="network_security",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail="CORS origins are explicit." if not wildcard else "CORS allows any origin.",
        threats=["I-01", "E-01"],
        requirements=["SR-NET-01"],
        mitigations=["Set CORS_ALLOWED_ORIGINS to the exact frontend origin list before production deployment."],
        evidence={"origins": origins, "allow_credentials": settings.cors_allow_credentials},
    )


def _default_users_check(settings: Settings, production: bool) -> SecurityControlCheck:
    default_users = _default_users_present(settings.users_store_path)
    demo_seed_enabled = bool(settings.seed_demo_users)
    ready = not default_users and not (production and demo_seed_enabled)
    if not production:
        ready = not default_users
    detail = "No seeded demo users found."
    if default_users:
        detail = f"Seeded demo users still exist: {', '.join(default_users)}."
    elif production and demo_seed_enabled:
        detail = "SEED_DEMO_USERS is enabled; production startup could create known demo credentials."
    return SecurityControlCheck(
        id="default_users",
        label="Seed demo users are removed for production",
        domain="authentication",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="critical",
        detail=detail,
        threats=["S-02", "E-01"],
        requirements=["SR-AUTH-02"],
        mitigations=["Set SEED_DEMO_USERS=false and replace seeded demo accounts with real users before production."],
        evidence={
            "path": str(settings.users_store_path),
            "default_users": default_users,
            "seed_demo_users": demo_seed_enabled,
        },
    )


def _store_backend_check(
    check_id: str,
    label: str,
    backend: str,
    production: bool,
    *,
    threats: list[str],
    requirements: list[str],
) -> SecurityControlCheck:
    normalized = backend.strip().lower()
    ready = normalized == "sqlite" or not production
    return SecurityControlCheck(
        id=check_id,
        label=label,
        domain="data_protection",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=f"{backend} backend configured." if ready else f"{backend} backend is not production-ready for durable audit state.",
        threats=threats,
        requirements=requirements,
        mitigations=["Use SQLite-backed stores for collaboration memory and speaker identity in production."],
        evidence={"backend": backend},
    )


def _workspace_check(settings: Settings, production: bool) -> SecurityControlCheck:
    raw_path = settings.voiceops_workspace
    path = Path(raw_path).expanduser() if raw_path else None
    exists = bool(path and path.exists() and path.is_dir())
    ready = exists or not production
    return SecurityControlCheck(
        id="workspace_boundary",
        label="Workspace path is explicit and exists",
        domain="authorization",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=str(path) if exists else "VOICEOPS_WORKSPACE is not configured or does not exist.",
        threats=["T-01", "E-02"],
        requirements=["SR-WORKSPACE-01"],
        mitigations=["Set VOICEOPS_WORKSPACE to the exact repository root and run the app with least-privilege filesystem access."],
        evidence={"configured": bool(raw_path), "path": str(path) if path else None, "exists": exists},
    )


def _runtime_artifact_paths_check(settings: Settings, production: bool) -> SecurityControlCheck:
    workspace = Path(settings.voiceops_workspace).expanduser().resolve(strict=False) if settings.voiceops_workspace else None
    artifacts = {
        "users": settings.users_store_path,
        "agent_runs": settings.agent_runs_path,
        "agent_llm_routes": settings.agent_llm_routes_path,
        "long_memory": settings.long_memory_path,
        "rag_index": settings.rag_index_path,
        "llm_connections": settings.llm_connection_store_path,
        "external_agent_credentials": settings.external_agent_store_path,
        "demo_evidence": settings.demo_evidence_path,
    }
    artifact_evidence: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    sensitive_artifacts = {
        "users",
        "agent_llm_routes",
        "agent_runs",
        "long_memory",
        "rag_index",
        "llm_connections",
        "external_agent_credentials",
    }
    for name, raw_path in artifacts.items():
        path = Path(raw_path).expanduser()
        resolved = path.resolve(strict=False)
        parent_exists = resolved.parent.exists()
        in_workspace = bool(workspace and _is_relative_to(resolved, workspace))
        secret_like = _secret_like_runtime_path(resolved)
        explicit = bool(str(raw_path).strip())
        mode = _file_mode(resolved)
        private_file = name not in sensitive_artifacts or mode in {None, "0o600"}
        ready = explicit and parent_exists and not in_workspace and not secret_like and private_file
        artifact_evidence[name] = {
            "path": str(path),
            "resolved": str(resolved),
            "parent_exists": parent_exists,
            "in_workspace": in_workspace,
            "secret_like": secret_like,
            "mode": mode,
            "private_file": private_file,
            "ready": ready,
        }
        if not ready:
            if not explicit:
                blockers.append(f"{name} path is empty")
            if not parent_exists:
                blockers.append(f"{name} parent directory does not exist")
            if in_workspace:
                blockers.append(f"{name} is inside VOICEOPS_WORKSPACE")
            if secret_like:
                blockers.append(f"{name} path looks like a secret file")
            if not private_file:
                blockers.append(f"{name} must be owner-readable only (0600)")

    ready = not blockers or not production
    if not blockers:
        detail = "Runtime artifact paths are isolated from the editable workspace."
    elif not production:
        detail = "Local mode permits non-isolated runtime artifacts; production blockers: " + "; ".join(blockers[:4])
    else:
        detail = "; ".join(blockers[:4])
    return SecurityControlCheck(
        id="runtime_artifact_paths",
        label="Runtime artifact paths are explicit and isolated",
        domain="data_protection",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=detail,
        threats=["T-02", "I-02", "I-04", "R-01"],
        requirements=["SR-RUNTIME-01", "SR-STORE-01", "SR-AUDIT-01", "SR-EXTAGENT-01"],
        mitigations=[
            "Set runtime store paths to a private data directory outside VOICEOPS_WORKSPACE and create the parent directories before production startup."
        ],
        evidence={"artifacts": artifact_evidence, "workspace": str(workspace) if workspace else None},
    )


def _speaker_provider_check(settings: Settings, production: bool) -> SecurityControlCheck:
    provider = settings.speaker_provider.strip().lower()
    hf_token_configured = _configured_secret(settings.hf_token)
    whisperx_ready = provider == "whisperx" and hf_token_configured
    ready = whisperx_ready or (not production and provider in {"mock", "whisperx"})
    detail = (
        "WhisperX provider and HF_TOKEN configured."
        if whisperx_ready
        else f"{provider} provider configured; production requires WhisperX with HF_TOKEN."
    )
    return SecurityControlCheck(
        id="speaker_provider",
        label="Speaker provider is production diarization capable",
        domain="data_protection",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="medium",
        detail=detail,
        threats=["S-03", "I-03"],
        requirements=["SR-SPEAKER-01"],
        mitigations=["Use SPEAKER_PROVIDER=whisperx with HF_TOKEN and accepted pyannote model terms for production demos."],
        evidence={"provider": provider, "hf_token_configured": hf_token_configured},
    )


def _github_side_effect_check(settings: Settings) -> SecurityControlCheck:
    allowlist = [branch for branch in settings.github_pr_allowed_base_branches if str(branch).strip()]
    cli_path = settings.github_pr_cli_path.strip()
    cli_available = bool(shutil.which(cli_path)) if cli_path else False
    cli_authenticated = _github_cli_authenticated(settings) if cli_available else False
    ready = (
        not settings.github_pr_creation_enabled
        or (
            bool(allowlist)
            and 0 < float(settings.github_pr_command_timeout_seconds) <= 120
            and cli_available
            and cli_authenticated
        )
    )
    return SecurityControlCheck(
        id="github_side_effects",
        label="GitHub external side effects are gated and auditable",
        domain="change_control",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=(
            "Real GitHub PR creation is disabled; dry-run plans only."
            if not settings.github_pr_creation_enabled
            else "Real GitHub PR creation is enabled with branch allowlist, command timeout, and authenticated GitHub CLI."
            if ready
            else "Real GitHub PR creation is enabled without required branch allowlist, command timeout controls, available GitHub CLI, or GitHub CLI authentication."
        ),
        threats=["T-03", "R-02"],
        requirements=["SR-CHANGE-01"],
        mitigations=[
            "Keep GITHUB_PR_CREATION_ENABLED=false unless GITHUB_PR_ALLOWED_BASE_BRANCHES, gh authentication, and audit controls are configured."
        ],
        evidence={
            "github_pr_creation_enabled": settings.github_pr_creation_enabled,
            "allowed_base_branches": allowlist,
            "cli_path": cli_path,
            "cli_available": cli_available,
            "cli_authenticated": cli_authenticated,
            "timeout_seconds": settings.github_pr_command_timeout_seconds,
            "draft": settings.github_pr_create_draft,
        },
    )


def _github_cli_authenticated(settings: Settings) -> bool:
    workspace = Path(settings.voiceops_workspace).expanduser() if settings.voiceops_workspace else Path.cwd()
    cwd = workspace if workspace.exists() else Path.cwd()
    try:
        result = subprocess.run(
            [settings.github_pr_cli_path.strip(), "auth", "status"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=min(float(settings.github_pr_command_timeout_seconds), 15.0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _external_agent_credentials_check(settings: Settings, production: bool) -> SecurityControlCheck:
    secret = settings.external_agent_credential_secret or ""
    ready = (_configured_secret(secret) and len(secret) >= 32 and secret != settings.jwt_secret) or not production
    return SecurityControlCheck(
        id="external_agent_credentials",
        label="External agent credentials use isolated encrypted storage",
        domain="data_protection",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=(
            "External agent credentials have an isolated encryption secret."
            if ready and secret
            else "Local mode may reuse JWT secret for credential encryption."
            if ready
            else "Production external agent credentials require EXTERNAL_AGENT_CREDENTIAL_SECRET distinct from JWT_SECRET."
        ),
        threats=["I-04", "T-04", "R-02"],
        requirements=["SR-EXTAGENT-01", "SR-CHANGE-01"],
        mitigations=[
            "Set EXTERNAL_AGENT_CREDENTIAL_SECRET to a generated 32+ character secret distinct from JWT_SECRET."
        ],
        evidence={
            "store_path": str(settings.external_agent_store_path),
            "secret_configured": _configured_secret(secret),
            "secret_distinct_from_jwt": bool(secret and secret != settings.jwt_secret),
            "allowed_providers": settings.external_agent_allowed_providers,
        },
    )


def _external_agent_execution_policy_check(settings: Settings, production: bool) -> SecurityControlCheck:
    allowed = [
        str(provider).strip().lower()
        for provider in settings.external_agent_allowed_providers
        if str(provider).strip()
    ]
    unknown = sorted(provider for provider in set(allowed) if provider not in SUPPORTED_EXTERNAL_AGENT_PROVIDERS)
    api_enabled = bool(settings.external_agent_api_execution_enabled)
    cli_enabled = bool(settings.external_agent_cli_execution_enabled)
    api_base_urls = {
        "anthropic": settings.anthropic_api_base_url,
        "openai": settings.openai_api_base_url,
    }
    insecure_api_urls = [
        name
        for name, url in api_base_urls.items()
        if api_enabled and not str(url).strip().lower().startswith("https://")
    ]
    timeout_blockers = []
    if not (0 < float(settings.external_agent_api_timeout_seconds) <= 120):
        timeout_blockers.append("API timeout must be between 0 and 120 seconds")
    if not (0 < int(settings.external_agent_cli_timeout_seconds) <= 300):
        timeout_blockers.append("CLI timeout must be between 0 and 300 seconds")

    blockers = []
    if not allowed:
        blockers.append("external agent provider allowlist is empty")
    if unknown:
        blockers.append(f"unknown providers configured: {', '.join(unknown)}")
    if production and insecure_api_urls:
        blockers.append(f"API execution uses non-HTTPS base URLs: {', '.join(insecure_api_urls)}")
    if production and settings.external_agent_oauth_mock_enabled:
        blockers.append("mock OAuth exchange is enabled")
    blockers.extend(timeout_blockers)

    ready = not blockers
    return SecurityControlCheck(
        id="external_agent_execution_policy",
        label="External agent execution policy is explicit and bounded",
        domain="change_control",
        ready=ready,
        status="ready" if ready else "needs_attention",
        severity="high",
        detail=(
            "External agent providers, execution modes, and timeouts are bounded."
            if ready
            else "; ".join(blockers)
        ),
        threats=["T-04", "I-04", "R-02"],
        requirements=["SR-EXTAGENT-01", "SR-CHANGE-01"],
        mitigations=[
            "Set EXTERNAL_AGENT_ALLOWED_PROVIDERS to supported providers only, keep API base URLs HTTPS "
            "in production, and bound external agent timeouts."
        ],
        evidence={
            "allowed_providers": allowed,
            "unknown_providers": unknown,
            "api_execution_enabled": api_enabled,
            "cli_execution_enabled": cli_enabled,
            "oauth_mock_enabled": settings.external_agent_oauth_mock_enabled,
            "api_timeout_seconds": settings.external_agent_api_timeout_seconds,
            "cli_timeout_seconds": settings.external_agent_cli_timeout_seconds,
            "api_base_urls_https": {
                name: str(url).strip().lower().startswith("https://")
                for name, url in api_base_urls.items()
            },
        },
    )


def _configured_secret(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return bool(normalized) and not any(marker in normalized for marker in PLACEHOLDER_SECRET_MARKERS)


def _default_users_present(path: Path) -> list[str]:
    try:
        raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    users = raw.get("users") if isinstance(raw, dict) else []
    found = []
    for item in users if isinstance(users, list) else []:
        email = str(item.get("email", "")).lower()
        if email in DEFAULT_USER_EMAILS:
            found.append(email)
    return sorted(found)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _secret_like_runtime_path(path: Path) -> bool:
    lower_name = path.name.lower()
    return (
        lower_name in {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"}
        or lower_name.startswith(".env.")
        or lower_name.endswith((".pem", ".key"))
    )


def _file_mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(stat.S_IMODE(path.stat().st_mode))


def _threats() -> list[SecurityThreat]:
    return [
        SecurityThreat(id="S-01", category="Spoofing", title="Forged JWT token", target="Auth/session", risk_level="Critical", controls=["jwt_secret"]),
        SecurityThreat(id="S-02", category="Spoofing", title="Known seeded demo credentials", target="User store", risk_level="Critical", controls=["default_users"]),
        SecurityThreat(id="S-03", category="Spoofing", title="Incorrect speaker attribution", target="Speaker identity", risk_level="High", controls=["speaker_provider"]),
        SecurityThreat(id="T-01", category="Tampering", title="Workspace path escape or wrong repo mutation", target="Workspace tools", risk_level="High", controls=["workspace_boundary"]),
        SecurityThreat(id="T-02", category="Tampering", title="Runtime JSON store modification", target="Collab/speaker stores", risk_level="High", controls=["collab_store", "speaker_store"]),
        SecurityThreat(id="T-03", category="Tampering", title="Unreviewed external PR side effect", target="GitHub adapter", risk_level="High", controls=["github_side_effects"]),
        SecurityThreat(id="R-01", category="Repudiation", title="Lost meeting approval audit", target="Collaboration memory", risk_level="High", controls=["collab_store"]),
        SecurityThreat(id="R-02", category="Repudiation", title="External action without auditable approval", target="GitHub adapter", risk_level="High", controls=["github_side_effects"]),
        SecurityThreat(id="I-01", category="Information Disclosure", title="Browser origin overexposure", target="CORS", risk_level="High", controls=["cors_origins"]),
        SecurityThreat(id="I-02", category="Information Disclosure", title="Sensitive meeting data in weak storage", target="Runtime stores", risk_level="High", controls=["collab_store", "speaker_store"]),
        SecurityThreat(id="I-03", category="Information Disclosure", title="Raw audio or speaker data mishandling", target="Speaker pipeline", risk_level="Medium", controls=["speaker_provider"]),
        SecurityThreat(id="I-04", category="Information Disclosure", title="External provider token disclosure", target="External agent credentials", risk_level="High", controls=["external_agent_credentials"]),
        SecurityThreat(id="I-05", category="Information Disclosure", title="Runtime artifacts written into editable workspace", target="Runtime data paths", risk_level="High", controls=["runtime_artifact_paths"]),
        SecurityThreat(id="T-04", category="Tampering", title="External agent bypasses approval workflow", target="External agent provider", risk_level="High", controls=["external_agent_credentials"]),
        SecurityThreat(id="T-05", category="Tampering", title="Workspace edits include runtime store files", target="Runtime data paths", risk_level="High", controls=["runtime_artifact_paths"]),
        SecurityThreat(id="E-01", category="Elevation of Privilege", title="Privilege escalation through weak auth boundary", target="Auth/API", risk_level="Critical", controls=["jwt_secret", "cors_origins", "default_users"]),
        SecurityThreat(id="E-02", category="Elevation of Privilege", title="Workspace mutation outside intended repo", target="Workspace tools", risk_level="High", controls=["workspace_boundary"]),
    ]


def _requirements() -> list[SecurityRequirement]:
    return [
        SecurityRequirement(
            id="SR-AUTH-01",
            title="Authentication tokens must use non-default production secrets",
            domain="authentication",
            priority="critical",
            threat_refs=["S-01", "E-01"],
            acceptance=["JWT_SECRET is non-default and at least 32 characters.", "Invalid or expired tokens are rejected."],
        ),
        SecurityRequirement(
            id="SR-AUTH-02",
            title="Seed demo users must not be present in production",
            domain="authentication",
            priority="critical",
            threat_refs=["S-02", "E-01"],
            acceptance=["Known demo emails are absent from the production user store.", "Bootstrap credentials are rotated before deployment."],
        ),
        SecurityRequirement(
            id="SR-NET-01",
            title="Production CORS must use explicit origins",
            domain="network_security",
            priority="high",
            threat_refs=["I-01", "E-01"],
            acceptance=["Wildcard CORS is rejected in production.", "Allowed origins are documented and environment-configured."],
        ),
        SecurityRequirement(
            id="SR-STORE-01",
            title="Durable collaboration stores must use production backend",
            domain="data_protection",
            priority="high",
            threat_refs=["T-02", "R-01", "I-02"],
            acceptance=["Collab and speaker stores use SQLite or stronger durable backend.", "Store paths are explicit and not committed runtime JSON."],
        ),
        SecurityRequirement(
            id="SR-AUDIT-01",
            title="Approval and handoff audit records must survive restart",
            domain="audit_logging",
            priority="high",
            threat_refs=["R-01", "R-02"],
            acceptance=["Approved/rejected actions include requester, approver, branch, files, and test result.", "Audit state is stored in a durable backend."],
        ),
        SecurityRequirement(
            id="SR-DATA-01",
            title="Speaker identity and meeting memory must stay scoped and durable",
            domain="data_protection",
            priority="high",
            threat_refs=["I-02", "I-03", "T-02"],
            acceptance=["Speaker mappings are room-scoped and auditable.", "Raw audio is not stored by default."],
        ),
        SecurityRequirement(
            id="SR-WORKSPACE-01",
            title="Workspace mutation must stay inside the configured repository",
            domain="authorization",
            priority="high",
            threat_refs=["T-01", "E-02"],
            acceptance=["VOICEOPS_WORKSPACE is explicit and exists.", "Path traversal attempts are rejected by workspace tools."],
        ),
        SecurityRequirement(
            id="SR-SPEAKER-01",
            title="Production speaker attribution must use real diarization evidence",
            domain="data_protection",
            priority="medium",
            threat_refs=["S-03", "I-03"],
            acceptance=["SPEAKER_PROVIDER=whisperx for production.", "HF_TOKEN is configured without storing raw audio by default."],
        ),
        SecurityRequirement(
            id="SR-CHANGE-01",
            title="External code collaboration side effects require explicit approval",
            domain="change_control",
            priority="high",
            threat_refs=["T-03", "T-04", "R-02"],
            acceptance=["GitHub PR creation remains dry-run until security review approves credentials and scopes.", "PR plans preserve action id and approval audit.", "External coding agents create pending approval actions rather than direct workspace writes."],
        ),
        SecurityRequirement(
            id="SR-EXTAGENT-01",
            title="External agent provider credentials must be encrypted and scoped",
            domain="data_protection",
            priority="high",
            threat_refs=["I-04", "T-04", "R-02"],
            acceptance=["Provider tokens are stored encrypted and never returned by APIs.", "Production uses an external-agent credential secret distinct from JWT_SECRET.", "Allowed external providers are explicitly configured."],
        ),
        SecurityRequirement(
            id="SR-RUNTIME-01",
            title="Runtime artifacts must be isolated from editable workspaces",
            domain="data_protection",
            priority="high",
            threat_refs=["I-05", "T-05", "R-01"],
            acceptance=[
                "Agent runs, LLM routes, long memory, RAG index, demo evidence, and credential stores use explicit paths.",
                "Runtime artifact parents exist before production startup.",
                "Runtime artifacts are outside VOICEOPS_WORKSPACE and do not use secret-like filenames.",
            ],
        ),
    ]
