from __future__ import annotations

import difflib
import re
import shlex
import shutil
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, status

from app.auth.models import UserPublic
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.external_agents.adapters import ExternalAgentExecutionAdapter, ExternalCodingAgentAdapter
from app.external_agents.models import (
    ExternalAgentApiKeyConnectRequest,
    ExternalAgentAuthMethod,
    ExternalAgentCapability,
    ExternalAgentCapabilityReport,
    ExternalAgentCredentialPublic,
    ExternalAgentCredentialRecord,
    ExternalAgentCredentialStatus,
    ExternalAgentLocalCliConnectRequest,
    ExternalAgentOAuthCallbackRequest,
    ExternalAgentOAuthStartRequest,
    ExternalAgentOAuthStartResponse,
    ExternalAgentPreflightMode,
    ExternalAgentPreflightReport,
    ExternalAgentProvider,
    ExternalAgentProviderPreflight,
    ExternalAgentProviderInfo,
    ExternalAgentRecommendationAlternative,
    ExternalAgentRecommendationRequest,
    ExternalAgentRecommendationResponse,
    ExternalAgentRunMode,
    ExternalAgentRunRequest,
    ExternalAgentRunResponse,
    ExternalAgentSetupGuideReport,
    ExternalAgentProviderSetupGuide,
    ExternalAgentSetupStep,
    ExternalAgentTaskKind,
)
from app.external_agents.security import decrypt_payload, encrypt_payload, preview_secret
from app.external_agents.service_labels import PROVIDER_LABELS
from app.external_agents.store import ExternalAgentCredentialStore
from app.redaction import redact_sensitive_text
from app.voice_agent.models import OrchestratorResult


PROVIDER_MODELS = {
    ExternalAgentProvider.CLAUDE: [
        "claude-fable-5",
        "claude-mythos-5",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-opus-4-8",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-sonnet",
        "claude-opus",
        "claude-haiku",
        "provider-default",
    ],
    ExternalAgentProvider.CODEX: [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-default",
        "provider-default",
    ],
    ExternalAgentProvider.CURSOR: [
        "cursor-default",
        "provider-default",
    ],
    ExternalAgentProvider.LOCAL: [
        "local-default",
        "glm-5.2-local",
        "qwen-coder-local",
        "deepseek-coder-local",
        "provider-default",
    ],
}

PROVIDER_DEFAULT_MODEL = {
    ExternalAgentProvider.CLAUDE: "claude-sonnet-4-6",
    ExternalAgentProvider.CODEX: "gpt-5.4",
    ExternalAgentProvider.CURSOR: "cursor-default",
    ExternalAgentProvider.LOCAL: "local-default",
}

PROVIDER_CAPABILITIES = [
    ExternalAgentRunMode.EXPLAIN,
    ExternalAgentRunMode.PATCH,
    ExternalAgentRunMode.REVIEW,
    ExternalAgentRunMode.TEST,
]

SAFE_LOCAL_CLI_ENV_KEYS = [
    "HOME",
    "LANG",
    "LC_*",
    "LOGNAME",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SHELL",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "VIRTUAL_ENV",
    "VOICEOPS_AGENT_MODE",
    "VOICEOPS_AGENT_MODEL",
    "VOICEOPS_AGENT_PROMPT",
    "VOICEOPS_AGENT_PROVIDER",
    "VOICEOPS_PROVIDER_RUN_ID",
    "VOICEOPS_WORKSPACE",
]

_pending_oauth_states: dict[str, dict] = {}
_OAUTH_STATE_TTL = timedelta(minutes=10)


class ExternalAgentService:
    def __init__(
        self,
        store: ExternalAgentCredentialStore,
        settings: Settings,
        collab: CollaborationService,
    ):
        self._store = store
        self._settings = settings
        self._collab = collab

    def with_settings(self, settings: Settings) -> "ExternalAgentService":
        return ExternalAgentService(self._store, settings, self._collab)

    def list_providers(self, user: UserPublic) -> list[ExternalAgentProviderInfo]:
        credentials = {record.provider: record for record in self._store.list_for_user(user.id)}
        allowed = set(self._settings.external_agent_allowed_providers)
        providers: list[ExternalAgentProviderInfo] = []
        for provider in ExternalAgentProvider:
            if provider.value not in allowed:
                continue
            credential = credentials.get(provider)
            providers.append(self._provider_info(provider, credential))
        return providers

    def preflight_report(self, user: UserPublic) -> ExternalAgentPreflightReport:
        providers = [
            provider.preflight
            for provider in self.list_providers(user)
            if provider.preflight is not None
        ]
        blockers = [
            f"{provider.label}: {blocker}"
            for provider in providers
            for blocker in provider.blockers
        ]
        warnings = [
            f"{provider.label}: {warning}"
            for provider in providers
            for warning in provider.warnings
        ]
        ready = not blockers
        return ExternalAgentPreflightReport(
            status="ready" if ready else "needs_attention",
            ready=ready,
            generated_at=_now(),
            providers=providers,
            blockers=blockers,
            warnings=warnings,
        )

    def setup_guide_report(self, user: UserPublic) -> ExternalAgentSetupGuideReport:
        providers = [
            provider.setup_guide
            for provider in self.list_providers(user)
            if provider.setup_guide is not None
        ]
        return ExternalAgentSetupGuideReport(generated_at=_now(), providers=providers)

    def capability_report(self, user: UserPublic) -> ExternalAgentCapabilityReport:
        providers = [self._provider_capability(provider) for provider in self.list_providers(user)]
        return ExternalAgentCapabilityReport(
            providers=providers,
            task_kinds=list(ExternalAgentTaskKind),
            generated_at=_now(),
        )

    def recommend(self, user: UserPublic, body: ExternalAgentRecommendationRequest) -> ExternalAgentRecommendationResponse:
        task_kind = _classify_task_kind(body.task, body.mode)
        mode = body.mode or _mode_for_task_kind(task_kind)
        requested_model = " ".join((body.model or "").strip().split()) or None
        providers = self.list_providers(user)
        if body.preferred_provider is not None:
            self._ensure_allowed(body.preferred_provider)
        candidates = [
            _score_recommendation_candidate(provider, task_kind, mode, preferred=body.preferred_provider)
            for provider in providers
        ]
        candidates.sort(key=lambda item: item[0], reverse=True)
        if not candidates:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No external agent providers are enabled")
        _, selected, reason, blockers = candidates[0]
        model = requested_model or selected.default_model or PROVIDER_DEFAULT_MODEL[selected.provider]
        alternatives = [
            ExternalAgentRecommendationAlternative(
                provider=provider.provider,
                label=provider.label,
                ready=_mode_ready(provider, mode),
                connected=provider.connected,
                score=score,
                reason=alt_reason,
                blockers=alt_blockers,
            )
            for score, provider, alt_reason, alt_blockers in candidates[:4]
        ]
        ready = _mode_ready(selected, mode)
        return ExternalAgentRecommendationResponse(
            task_kind=task_kind,
            provider=selected.provider,
            label=selected.label,
            mode=mode,
            model=model,
            ready=ready,
            connected=selected.connected,
            approval_required=mode == ExternalAgentRunMode.PATCH,
            confidence=_recommendation_confidence(candidates),
            reason=reason,
            blockers=blockers,
            alternatives=alternatives,
        )

    def connect_api_key(
        self,
        provider: ExternalAgentProvider,
        user: UserPublic,
        body: ExternalAgentApiKeyConnectRequest,
    ) -> ExternalAgentCredentialPublic:
        self._ensure_allowed(provider)
        _ensure_auth_method_supported(provider, ExternalAgentAuthMethod.API_KEY)
        now = _now()
        encrypted = encrypt_payload(
            self._settings,
            {
                "auth_method": ExternalAgentAuthMethod.API_KEY.value,
                "api_key": body.api_key,
            },
        )
        record = ExternalAgentCredentialRecord(
            id=f"xag-{uuid4().hex[:12]}",
            user_id=user.id,
            provider=provider,
            auth_method=ExternalAgentAuthMethod.API_KEY,
            account_label=body.account_label or PROVIDER_LABELS[provider],
            encrypted_payload=encrypted,
            token_preview=preview_secret(body.api_key),
            scopes=body.scopes,
            created_at=now,
            updated_at=now,
            metadata={"source": "api_key"},
        )
        return _public(self._store.upsert(record))

    def connect_local_cli(
        self,
        provider: ExternalAgentProvider,
        user: UserPublic,
        body: ExternalAgentLocalCliConnectRequest,
    ) -> ExternalAgentCredentialPublic:
        self._ensure_allowed(provider)
        _ensure_auth_method_supported(provider, ExternalAgentAuthMethod.LOCAL_CLI)
        now = _now()
        command = body.command.strip() or _default_cli_command(provider)
        default_template = _default_command_template(provider)
        if default_template and command != _default_cli_command(provider):
            default_template = f"{command} --workspace {{workspace}} --prompt {{prompt}} --model {{model}}"
        command_template = " ".join((body.command_template or default_template).strip().split()) or None
        _reject_sensitive_local_cli_text(command)
        _validate_local_cli_template(command_template)
        encrypted = encrypt_payload(
            self._settings,
            {
                "auth_method": ExternalAgentAuthMethod.LOCAL_CLI.value,
                "command": command,
                "command_template": command_template,
            },
        )
        record = ExternalAgentCredentialRecord(
            id=f"xag-{uuid4().hex[:12]}",
            user_id=user.id,
            provider=provider,
            auth_method=ExternalAgentAuthMethod.LOCAL_CLI,
            account_label=body.account_label or f"{PROVIDER_LABELS[provider]} local CLI",
            encrypted_payload=encrypted,
            token_preview=None,
            scopes=["local_cli"],
            created_at=now,
            updated_at=now,
            metadata={"source": "local_cli", "command": command, "command_template": command_template},
        )
        return _public(self._store.upsert(record))

    def start_oauth(
        self,
        provider: ExternalAgentProvider,
        user: UserPublic,
        body: ExternalAgentOAuthStartRequest,
    ) -> ExternalAgentOAuthStartResponse:
        self._ensure_allowed(provider)
        _ensure_auth_method_supported(provider, ExternalAgentAuthMethod.OAUTH)
        _prune_expired_oauth_states()
        state = secrets.token_urlsafe(24)
        redirect_uri = _oauth_redirect_uri(self._settings, provider, body.redirect_uri)
        authorize_url = _oauth_authorize_url(self._settings, provider)
        client_id = _oauth_client_id(self._settings, provider)
        mode = "configured" if client_id and _oauth_token_url(self._settings, provider) else "mock_exchange"
        if mode == "mock_exchange" and not self._settings.external_agent_oauth_mock_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{provider.value} OAuth is not configured. Use API key or local CLI, or configure the provider OAuth client.",
            )
        _pending_oauth_states[state] = {
            "provider": provider.value,
            "user_id": user.id,
            "scopes": body.scopes,
            "redirect_uri": redirect_uri,
            "mode": mode,
            "created_at": _now().isoformat(),
        }
        query = urlencode(
            {
                "client_id": client_id or f"voiceops-{provider.value}-placeholder",
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(body.scopes or _default_scopes(provider)),
                "state": state,
            }
        )
        return ExternalAgentOAuthStartResponse(
            provider=provider,
            authorization_url=f"{authorize_url}?{query}",
            state=state,
            redirect_uri=redirect_uri,
            mode=mode,
            detail=(
                "OAuth client and token endpoint are configured."
                if mode == "configured"
                else "Provider OAuth endpoint is not configured yet; callback will store a mock exchange credential for local integration tests."
            ),
        )

    async def complete_oauth(
        self,
        user: UserPublic,
        body: ExternalAgentOAuthCallbackRequest,
    ) -> ExternalAgentCredentialPublic:
        return await self._complete_oauth_for_user(user.id, body)

    async def complete_oauth_redirect(
        self,
        provider: ExternalAgentProvider,
        *,
        code: str,
        state: str,
    ) -> ExternalAgentCredentialPublic:
        return await self._complete_oauth_for_user(
            "",
            ExternalAgentOAuthCallbackRequest(provider=provider, code=code, state=state),
            trust_state_user=True,
        )

    async def _complete_oauth_for_user(
        self,
        user_id: str,
        body: ExternalAgentOAuthCallbackRequest,
        *,
        trust_state_user: bool = False,
    ) -> ExternalAgentCredentialPublic:
        self._ensure_allowed(body.provider)
        _prune_expired_oauth_states()
        pending = _pending_oauth_states.pop(body.state, None)
        state_user_id = str((pending or {}).get("user_id") or "")
        if not pending or pending["provider"] != body.provider.value or (not trust_state_user and state_user_id != user_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
        # ponytail: browser OAuth callback has no bearer token; the unguessable state binds it to the starter user.
        credential_user_id = state_user_id if trust_state_user else user_id
        now = _now()
        mode = pending["mode"]
        token_payload = (
            await _exchange_oauth_token(self._settings, body.provider, body.code, pending["redirect_uri"])
            if mode == "configured"
            else {
                "access_token": f"mock-{body.provider.value}-{body.code}",
                "refresh_token": None,
                "expires_in": None,
                "exchange_mode": mode,
            }
        )
        token = token_payload["access_token"]
        expires_at = (
            now + timedelta(seconds=int(token_payload["expires_in"]))
            if token_payload.get("expires_in")
            else None
        )
        encrypted = encrypt_payload(
            self._settings,
            {
                "auth_method": ExternalAgentAuthMethod.OAUTH.value,
                "access_token": token,
                "refresh_token": token_payload.get("refresh_token"),
                "exchange_mode": mode,
            },
        )
        record = ExternalAgentCredentialRecord(
            id=f"xag-{uuid4().hex[:12]}",
            user_id=credential_user_id,
            provider=body.provider,
            auth_method=ExternalAgentAuthMethod.OAUTH,
            account_label=body.account_label or f"{PROVIDER_LABELS[body.provider]} OAuth",
            encrypted_payload=encrypted,
            token_preview=preview_secret(token),
            scopes=pending.get("scopes") or _default_scopes(body.provider),
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            metadata={"source": "oauth", "exchange_mode": mode},
        )
        return _public(self._store.upsert(record))

    def disconnect(self, provider: ExternalAgentProvider, user: UserPublic) -> bool:
        self._ensure_allowed(provider)
        return self._store.delete(user.id, provider.value)

    def preflight_run(
        self,
        user: UserPublic,
        provider: ExternalAgentProvider,
        mode: ExternalAgentRunMode,
    ) -> dict:
        self._ensure_allowed(provider)
        credential = self._store.get(user.id, provider.value)
        readiness = _provider_mode_readiness(self._settings, provider, credential)
        return readiness.get(mode.value) or _readiness(True, "ready", "Mode is available.")

    def run(
        self,
        room_id: str,
        user: UserPublic,
        body: ExternalAgentRunRequest,
    ) -> ExternalAgentRunResponse:
        self._ensure_allowed(body.provider)
        preflight = self.preflight_run(user, body.provider, body.mode)
        if not preflight.get("ready"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=preflight.get("detail") or preflight.get("reason") or "External agent mode is not ready")
        credential = self._store.get(user.id, body.provider.value)
        if credential is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{body.provider.value} is not connected")
        try:
            payload = decrypt_payload(self._settings, credential.encrypted_payload)
        except (InvalidToken, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="External agent credential cannot be decrypted; reconnect the provider.",
            ) from exc
        provider_run_id = f"xrun-{uuid4().hex[:12]}"
        selected_model = _selected_model(body.provider, body.model)
        if body.mode == ExternalAgentRunMode.PATCH:
            result = self._propose_patch(provider_run_id, credential, payload, body, model=selected_model)
            action = self._collab.add_action(room_id, user, result)
            if action is None:
                raise HTTPException(status_code=500, detail="External agent did not create an action")
            self._collab.add_agent_message(
                room_id,
                f"{PROVIDER_LABELS[body.provider]} proposed a patch and is waiting for approval.",
                metadata={
                    "source": "external_agent_run",
                    "provider": body.provider.value,
                    "provider_run_id": provider_run_id,
                    "action_id": action.id,
                    "mode": body.mode.value,
                    "model": selected_model,
                    "auth_method": credential.auth_method.value,
                },
            )
            return ExternalAgentRunResponse(
                provider=body.provider,
                provider_run_id=provider_run_id,
                mode=body.mode,
                model=selected_model,
                status="pending_approval",
                summary=result.summary,
                action_id=action.id,
                files_changed=result.files_changed,
                diff=result.approval.get("diff"),
                audit={
                    "auth_method": credential.auth_method.value,
                    "account_label": credential.account_label,
                    "model": selected_model,
                    "runtime": result.approval.get("runtime"),
                },
            )

        adapter_result = ExternalAgentExecutionAdapter(self._settings).run_read_only(
            provider_run_id=provider_run_id,
            credential=credential,
            payload=payload,
            body=body,
            model=selected_model,
        )
        message = self._collab.add_agent_message(
            room_id,
            adapter_result.summary,
            metadata={
                "source": "external_agent_run",
                "provider": body.provider.value,
                "provider_run_id": provider_run_id,
                "mode": body.mode.value,
                "model": selected_model,
                "status": adapter_result.status,
                "auth_method": credential.auth_method.value,
                "execution_mode": adapter_result.audit.get("execution_mode"),
            },
        )
        return ExternalAgentRunResponse(
            provider=body.provider,
            provider_run_id=provider_run_id,
            mode=body.mode,
            model=selected_model,
            status=adapter_result.status,
            summary=message.text,
            message_id=message.id,
            audit={
                "auth_method": credential.auth_method.value,
                "account_label": credential.account_label,
                "model": selected_model,
                **adapter_result.audit,
            },
        )

    def _propose_patch(
        self,
        provider_run_id: str,
        credential: ExternalAgentCredentialRecord,
        payload: dict,
        body: ExternalAgentRunRequest,
        *,
        model: str,
    ) -> OrchestratorResult:
        if credential.auth_method != ExternalAgentAuthMethod.LOCAL_CLI:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Patch mode requires a local CLI credential so the coding agent can prepare a diff in an isolated temp workspace.",
            )
        if not self._settings.voiceops_workspace:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VOICEOPS_WORKSPACE is required for external patch runs")
        workspace = Path(self._settings.voiceops_workspace).expanduser()
        if not workspace.exists() or not workspace.is_dir():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The configured workspace directory is not available")

        adapter_result = None
        if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI and self._settings.external_agent_cli_execution_enabled:
            adapter_result = ExternalCodingAgentAdapter(self._settings).propose_patch(
                provider_run_id=provider_run_id,
                credential=credential,
                payload=payload,
                body=body,
                model=model,
            )
            if adapter_result.status != "completed":
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=adapter_result.summary,
                )

        if adapter_result is not None:
            diff = adapter_result.diff
            proposed_files = adapter_result.proposed_files
            changed_files = adapter_result.files_changed
            summary = adapter_result.summary
            test_command = adapter_result.test_command
            execution_audit = adapter_result.audit
        else:
            target = workspace / "app.py"
            if not target.exists():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="External patch runs require app.py in the configured workspace")
            before = target.read_text(encoding="utf-8") if target.exists() else ""
            after = _patch_app_py(before)
            diff = "\n".join(
                difflib.unified_diff(
                    before.splitlines(),
                    after.splitlines(),
                    fromfile="a/app.py",
                    tofile="b/app.py",
                    lineterm="",
                )
            )
            proposed_files = {"app.py": after}
            changed_files = ["app.py"]
            test_command = "python -m pytest -q"
            summary = f"{PROVIDER_LABELS[body.provider]} prepared a patch for app.py from an external agent run."
            execution_audit = {
                "execution_mode": "mock_patch_adapter",
                "detail": "External CLI patch execution is disabled; using deterministic local demo patch.",
            }

        approval = {
            "status": "pending_approval",
            "diff": diff,
            "test_command": test_command,
            "provider": body.provider.value,
            "provider_run_id": provider_run_id,
            "model": model,
            "external_agent": {
                "provider": body.provider.value,
                "model": model,
                "auth_method": credential.auth_method.value,
                "account_label": credential.account_label,
                "credential_id": credential.id,
                "exchange_mode": payload.get("exchange_mode"),
            },
            "route": "external_agent_provider",
            "policy": "approval_first",
            "runtime": execution_audit,
        }
        return OrchestratorResult(
            executed=True,
            action="patch",
            summary=summary,
            artifacts=[
                {
                    "kind": "diff",
                    "provider": body.provider.value,
                    "provider_run_id": provider_run_id,
                    "model": model,
                    "content": diff,
                }
            ],
            files_changed=changed_files,
            pending_approval=True,
            approval=approval,
            approval_payload={
                "kind": "patch",
                "files": proposed_files,
                "test_command": test_command,
            },
        )

    def _provider_info(
        self,
        provider: ExternalAgentProvider,
        credential: ExternalAgentCredentialRecord | None,
    ) -> ExternalAgentProviderInfo:
        if credential:
            mode_readiness = _provider_mode_readiness(self._settings, provider, credential)
            preflight = _provider_preflight(
                self._settings,
                provider,
                mode_readiness,
                credential=credential,
            )
            local_cli_command = None
            local_cli_command_template = None
            if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI:
                local_cli_command = str(credential.metadata.get("command") or "").strip() or None
                local_cli_command_template = str(credential.metadata.get("command_template") or "").strip() or None
            return ExternalAgentProviderInfo(
                provider=provider,
                label=PROVIDER_LABELS[provider],
                status=ExternalAgentCredentialStatus.CONNECTED,
                connected=True,
                auth_methods=_provider_auth_methods(provider),
                capabilities=PROVIDER_CAPABILITIES,
                credential_id=credential.id,
                auth_method=credential.auth_method,
                account_label=credential.account_label,
                token_preview=credential.token_preview,
                last_connected_at=credential.updated_at,
                detail=f"Connected with {credential.auth_method.value}. Tokens are stored encrypted and never returned.",
                supported_models=PROVIDER_MODELS[provider],
                default_model=PROVIDER_DEFAULT_MODEL[provider],
                mode_readiness=mode_readiness,
                preflight=preflight,
                setup_guide=_provider_setup_guide(
                    self._settings,
                    provider,
                    preflight,
                    credential=credential,
                ),
                local_cli_command=local_cli_command,
                local_cli_command_template=local_cli_command_template,
            )
        mode_readiness = _provider_mode_readiness(self._settings, provider, None)
        preflight = _provider_preflight(self._settings, provider, mode_readiness, credential=None)
        return ExternalAgentProviderInfo(
            provider=provider,
            label=PROVIDER_LABELS[provider],
            status=ExternalAgentCredentialStatus.DISCONNECTED,
            connected=False,
            auth_methods=_provider_auth_methods(provider),
            capabilities=PROVIDER_CAPABILITIES,
            detail="Not connected. Use OAuth, API key, or local CLI auth.",
            supported_models=PROVIDER_MODELS[provider],
            default_model=PROVIDER_DEFAULT_MODEL[provider],
            mode_readiness=mode_readiness,
            preflight=preflight,
            setup_guide=_provider_setup_guide(self._settings, provider, preflight, credential=None),
        )

    def _provider_capability(self, info: ExternalAgentProviderInfo) -> ExternalAgentCapability:
        return ExternalAgentCapability(
            provider=info.provider,
            label=info.label,
            connected=info.connected,
            auth_methods=info.auth_methods,
            supported_models=info.supported_models,
            default_model=info.default_model,
            task_kinds=_task_kinds_for_provider(info.provider),
            modes=PROVIDER_CAPABILITIES,
            patch_requires_approval=True,
            patch_requires_local_cli=True,
            best_for=_best_for_provider(info.provider),
            limitations=_provider_limitations(info),
            mode_readiness=info.mode_readiness,
        )

    def _ensure_allowed(self, provider: ExternalAgentProvider) -> None:
        if provider.value not in set(self._settings.external_agent_allowed_providers):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"External agent provider is not enabled: {provider.value}")


def get_external_agent_service(
    settings: Settings = Depends(get_settings),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> ExternalAgentService:
    return ExternalAgentService(
        ExternalAgentCredentialStore(settings.external_agent_store_path),
        settings,
        collab,
    )


def _public(record: ExternalAgentCredentialRecord) -> ExternalAgentCredentialPublic:
    return ExternalAgentCredentialPublic(
        id=record.id,
        provider=record.provider,
        auth_method=record.auth_method,
        account_label=record.account_label,
        token_preview=record.token_preview,
        scopes=record.scopes,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
        metadata={key: value for key, value in record.metadata.items() if key not in {"access_token", "refresh_token"}},
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _prune_expired_oauth_states() -> None:
    cutoff = _now() - _OAUTH_STATE_TTL
    for state, pending in list(_pending_oauth_states.items()):
        try:
            created_at = datetime.fromisoformat(str(pending.get("created_at")))
        except ValueError:
            created_at = datetime.min.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < cutoff:
            _pending_oauth_states.pop(state, None)


def _default_scopes(provider: ExternalAgentProvider) -> list[str]:
    if provider in {ExternalAgentProvider.CURSOR, ExternalAgentProvider.LOCAL}:
        return ["local_cli"]
    if provider == ExternalAgentProvider.CODEX:
        return ["codex:run", "workspace:read"]
    return ["claude_code:run", "workspace:read"]


def _default_cli_command(provider: ExternalAgentProvider) -> str:
    if provider == ExternalAgentProvider.CLAUDE:
        return "claude"
    if provider == ExternalAgentProvider.CODEX:
        return "codex"
    if provider == ExternalAgentProvider.LOCAL:
        return "local-agent"
    return "cursor-agent"


def _provider_auth_methods(provider: ExternalAgentProvider) -> list[ExternalAgentAuthMethod]:
    if provider in {ExternalAgentProvider.CURSOR, ExternalAgentProvider.LOCAL}:
        return [ExternalAgentAuthMethod.LOCAL_CLI]
    return list(ExternalAgentAuthMethod)


def _ensure_auth_method_supported(
    provider: ExternalAgentProvider,
    auth_method: ExternalAgentAuthMethod,
) -> None:
    if auth_method not in _provider_auth_methods(provider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{PROVIDER_LABELS[provider]} supports {', '.join(method.value for method in _provider_auth_methods(provider))} credentials only.",
        )


def _selected_model(provider: ExternalAgentProvider, requested: str | None) -> str:
    model = " ".join((requested or "").strip().split())
    if model:
        return model[:160]
    return PROVIDER_DEFAULT_MODEL[provider]


def _classify_task_kind(task: str, mode: ExternalAgentRunMode | None) -> ExternalAgentTaskKind:
    text = f"{mode.value if mode else ''} {task}".lower()
    if mode == ExternalAgentRunMode.PATCH:
        return ExternalAgentTaskKind.WRITE_PATCH
    if mode == ExternalAgentRunMode.TEST:
        return ExternalAgentTaskKind.RUN_TESTS
    if mode == ExternalAgentRunMode.REVIEW:
        return ExternalAgentTaskKind.REVIEW_PATCH
    if "git status" in text or "git diff" in text or "branch" in text:
        return ExternalAgentTaskKind.GIT_STATUS
    if "summarize" in text or "summary" in text or "what did" in text or "changed" in text:
        return ExternalAgentTaskKind.SUMMARIZE_CHANGES
    if "run test" in text or "pytest" in text or "npm test" in text or "test suite" in text:
        return ExternalAgentTaskKind.RUN_TESTS
    if "find bug" in text or "bug" in text or "diagnose" in text or "why fail" in text or "debug" in text:
        return ExternalAgentTaskKind.FIND_BUG
    if "review" in text or "inspect" in text:
        return ExternalAgentTaskKind.REVIEW_PATCH
    if any(keyword in text for keyword in ("fix", "implement", "patch", "write code", "change", "add endpoint")):
        return ExternalAgentTaskKind.WRITE_PATCH
    return ExternalAgentTaskKind.EXPLAIN_CODE


def _mode_for_task_kind(task_kind: ExternalAgentTaskKind) -> ExternalAgentRunMode:
    if task_kind == ExternalAgentTaskKind.WRITE_PATCH:
        return ExternalAgentRunMode.PATCH
    if task_kind == ExternalAgentTaskKind.RUN_TESTS:
        return ExternalAgentRunMode.TEST
    if task_kind in {ExternalAgentTaskKind.FIND_BUG, ExternalAgentTaskKind.REVIEW_PATCH}:
        return ExternalAgentRunMode.REVIEW
    return ExternalAgentRunMode.EXPLAIN


def _task_kinds_for_provider(provider: ExternalAgentProvider) -> list[ExternalAgentTaskKind]:
    if provider == ExternalAgentProvider.LOCAL:
        return [
            ExternalAgentTaskKind.EXPLAIN_CODE,
            ExternalAgentTaskKind.FIND_BUG,
            ExternalAgentTaskKind.REVIEW_PATCH,
            ExternalAgentTaskKind.WRITE_PATCH,
            ExternalAgentTaskKind.RUN_TESTS,
        ]
    return list(ExternalAgentTaskKind)


def _best_for_provider(provider: ExternalAgentProvider) -> list[str]:
    if provider == ExternalAgentProvider.CLAUDE:
        return ["large-code explanation", "review", "architecture reasoning"]
    if provider == ExternalAgentProvider.CODEX:
        return ["code patch proposals", "test-driven implementation", "repository edits"]
    if provider == ExternalAgentProvider.CURSOR:
        return ["workspace-aware coding", "IDE-oriented edits", "patch proposals"]
    return ["open-source/self-hosted coding agent", "offline experiments", "custom local models"]


def _provider_limitations(info: ExternalAgentProviderInfo) -> list[str]:
    limitations: list[str] = []
    patch_readiness = info.mode_readiness.get(ExternalAgentRunMode.PATCH.value)
    if patch_readiness and not patch_readiness.ready:
        limitations.append(patch_readiness.detail)
    if info.provider == ExternalAgentProvider.LOCAL:
        limitations.append("Local provider is CLI-only; OAuth and API key auth are intentionally disabled.")
    if not info.connected:
        limitations.append("Provider must be connected before it can run.")
    return _unique(limitations)


def _score_recommendation_candidate(
    provider: ExternalAgentProviderInfo,
    task_kind: ExternalAgentTaskKind,
    mode: ExternalAgentRunMode,
    *,
    preferred: ExternalAgentProvider | None,
) -> tuple[int, ExternalAgentProviderInfo, str, list[str]]:
    readiness = provider.mode_readiness.get(mode.value)
    ready = bool(readiness and readiness.ready)
    score = 0
    if provider.connected:
        score += 40
    if ready:
        score += 35
    if provider.provider == preferred:
        score += 30
    if task_kind == ExternalAgentTaskKind.WRITE_PATCH and provider.auth_method == ExternalAgentAuthMethod.LOCAL_CLI:
        score += 20
    if task_kind in {ExternalAgentTaskKind.EXPLAIN_CODE, ExternalAgentTaskKind.FIND_BUG, ExternalAgentTaskKind.REVIEW_PATCH}:
        if provider.provider == ExternalAgentProvider.CLAUDE:
            score += 12
        elif provider.provider == ExternalAgentProvider.CODEX:
            score += 10
    if task_kind in {ExternalAgentTaskKind.WRITE_PATCH, ExternalAgentTaskKind.RUN_TESTS}:
        if provider.provider == ExternalAgentProvider.CODEX:
            score += 12
        elif provider.provider == ExternalAgentProvider.LOCAL:
            score += 10
        elif provider.provider == ExternalAgentProvider.CURSOR:
            score += 8
    if task_kind in {ExternalAgentTaskKind.SUMMARIZE_CHANGES, ExternalAgentTaskKind.GIT_STATUS}:
        if provider.provider == ExternalAgentProvider.CODEX:
            score += 8
        elif provider.provider == ExternalAgentProvider.CURSOR:
            score += 6
    blockers = [] if ready else [readiness.detail if readiness else f"{mode.value} mode is not available."]
    reason = (
        f"{provider.label} is ready for {mode.value} mode."
        if ready
        else f"{provider.label} is the best available candidate, but setup is still required."
    )
    return score, provider, reason, blockers


def _mode_ready(provider: ExternalAgentProviderInfo, mode: ExternalAgentRunMode) -> bool:
    readiness = provider.mode_readiness.get(mode.value)
    return bool(readiness and readiness.ready)


def _recommendation_confidence(candidates: list[tuple[int, ExternalAgentProviderInfo, str, list[str]]]) -> float:
    if not candidates:
        return 0.0
    top = candidates[0][0]
    runner_up = candidates[1][0] if len(candidates) > 1 else 0
    if top <= 0:
        return 0.2
    margin = max(0, top - runner_up)
    return min(0.95, 0.55 + (margin / 100))


def _provider_setup_guide(
    settings: Settings,
    provider: ExternalAgentProvider,
    preflight: ExternalAgentProviderPreflight,
    *,
    credential: ExternalAgentCredentialRecord | None,
) -> ExternalAgentProviderSetupGuide:
    steps = _base_setup_steps(settings, provider, credential)
    constraints = [
        "Patch mode always stays preview-first: the provider proposes a diff, then a teammate approves before workspace writes.",
        "API key and OAuth credentials stay encrypted server-side; the browser only receives token previews.",
        "Local CLI patch runs execute in a temporary workspace before approval.",
    ]
    notes: list[str] = []
    state = preflight.state
    recommended_path = "local_cli_for_code_changes"
    next_step = "Provider is ready for assignment."

    if credential is None:
        state = "not_connected"
        next_step = (
            "Connect a local CLI command for this open-source or self-hosted coding agent."
            if provider == ExternalAgentProvider.LOCAL
            else "Connect local CLI for code-changing work, or API/OAuth for read-only explain and review."
        )
        notes.append("Use local CLI if this provider should write patches through its own coding agent.")
    elif credential.auth_method in {ExternalAgentAuthMethod.API_KEY, ExternalAgentAuthMethod.OAUTH}:
        recommended_path = "api_or_oauth_read_only"
        if any("local CLI credential" in blocker for blocker in preflight.blockers):
            next_step = "Add a local CLI credential before assigning patch work."
            notes.append("This credential can run read-only modes; patch needs the provider CLI.")
        else:
            next_step = "Read-only modes are available; keep patch work on a local CLI provider."
    elif credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI:
        cli_available = preflight.evidence.get("cli_available")
        cli_command = preflight.evidence.get("cli_command") or _default_cli_command(provider)
        if not settings.voiceops_workspace:
            next_step = "Bootstrap a git workspace before assigning patch work."
        elif settings.external_agent_cli_execution_enabled and cli_available is False:
            next_step = f"Install or fix the local command on PATH: {cli_command}."
        elif not settings.external_agent_cli_execution_enabled:
            next_step = "Enable CLI execution when you are ready to run the real local coding agent."
            notes.append("Current local development can still use deterministic read-only/demo patch behavior.")
        elif preflight.blockers:
            next_step = preflight.blockers[0]
        else:
            next_step = "Ready for preview-first patch, review, explain, and test modes."

    if not settings.voiceops_workspace:
        steps.append(_workspace_step())
    if credential and credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI and not settings.external_agent_cli_execution_enabled:
        steps.append(
            ExternalAgentSetupStep(
                id="enable_cli_runtime",
                label="Enable real CLI runtime",
                detail="Set EXTERNAL_AGENT_CLI_EXECUTION_ENABLED=true after the team trusts local sandbox execution.",
                action="set_env",
                recommended=False,
                auth_method=ExternalAgentAuthMethod.LOCAL_CLI,
                modes=[ExternalAgentRunMode.EXPLAIN, ExternalAgentRunMode.PATCH, ExternalAgentRunMode.REVIEW, ExternalAgentRunMode.TEST],
                command="EXTERNAL_AGENT_CLI_EXECUTION_ENABLED=true",
            )
        )

    return ExternalAgentProviderSetupGuide(
        provider=provider,
        label=PROVIDER_LABELS[provider],
        state=state,
        recommended_path=recommended_path,
        next_step=next_step,
        steps=steps,
        notes=notes,
        constraints=constraints,
    )


def _base_setup_steps(
    settings: Settings,
    provider: ExternalAgentProvider,
    credential: ExternalAgentCredentialRecord | None,
) -> list[ExternalAgentSetupStep]:
    local_cli_recommended = credential is None or credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI
    steps: list[ExternalAgentSetupStep] = [
        ExternalAgentSetupStep(
            id="connect_local_cli",
            label="Connect local CLI",
            detail=(
                "Use this for real code changes. VoiceOps runs the provider in a temporary workspace, "
                "captures a diff, and waits for teammate approval."
            ),
            action="connect_local_cli",
            recommended=local_cli_recommended,
            auth_method=ExternalAgentAuthMethod.LOCAL_CLI,
            modes=[ExternalAgentRunMode.EXPLAIN, ExternalAgentRunMode.PATCH, ExternalAgentRunMode.REVIEW, ExternalAgentRunMode.TEST],
            command=_default_cli_command(provider),
            metadata={"command_template": _default_command_template(provider)},
        )
    ]
    if provider in {ExternalAgentProvider.CURSOR, ExternalAgentProvider.LOCAL}:
        return steps
    steps.extend([
        ExternalAgentSetupStep(
            id="connect_oauth",
            label="Connect OAuth",
            detail="Use this for provider-account access when OAuth is configured. Patch mode still requires local CLI.",
            action="start_oauth",
            recommended=credential is None,
            auth_method=ExternalAgentAuthMethod.OAUTH,
            modes=[ExternalAgentRunMode.EXPLAIN, ExternalAgentRunMode.REVIEW, ExternalAgentRunMode.TEST],
            metadata={
                "configured": bool(_oauth_client_id(settings, provider) and _oauth_token_url(settings, provider)),
                "scopes": _default_scopes(provider),
            },
        ),
        ExternalAgentSetupStep(
            id="connect_api_key",
            label="Connect API key",
            detail="Use this for read-only explain, review, and test-plan runs. It cannot prepare file diffs.",
            action="connect_api_key",
            recommended=False,
            auth_method=ExternalAgentAuthMethod.API_KEY,
            modes=[ExternalAgentRunMode.EXPLAIN, ExternalAgentRunMode.REVIEW, ExternalAgentRunMode.TEST],
        ),
    ])
    return steps


def _workspace_step() -> ExternalAgentSetupStep:
    return ExternalAgentSetupStep(
        id="bootstrap_workspace",
        label="Bootstrap workspace",
        detail="Configure VOICEOPS_WORKSPACE to a git checkout before assigning patch work.",
        action="configure_workspace",
        recommended=True,
        modes=[ExternalAgentRunMode.PATCH],
        command="cd backend && python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json",
    )


def _default_command_template(provider: ExternalAgentProvider) -> str:
    if provider == ExternalAgentProvider.CODEX:
        return ""
    return f"{_default_cli_command(provider)} --workspace {{workspace}} --prompt {{prompt}} --model {{model}}"


def _validate_local_cli_template(command_template: str | None) -> None:
    if not command_template:
        return
    _reject_sensitive_local_cli_text(command_template)
    if "{workspace}" not in command_template:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Local CLI command_template must include {workspace}.")
    try:
        shlex.split(command_template)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid local CLI command_template: {exc}") from exc


def _reject_sensitive_local_cli_text(value: str) -> None:
    lowered = value.lower()
    if redact_sensitive_text(value) != value or re.search(r"\b(?:cookie|password|session[_-]?(?:id|token))\b", lowered):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Local CLI commands must not include tokens, passwords, or browser cookies. Use the provider's local login instead.",
        )


def _provider_preflight(
    settings: Settings,
    provider: ExternalAgentProvider,
    readiness_by_mode: dict[str, dict],
    *,
    credential: ExternalAgentCredentialRecord | None,
) -> ExternalAgentProviderPreflight:
    modes = [
        _preflight_mode(mode, readiness_by_mode.get(mode.value) or {})
        for mode in PROVIDER_CAPABILITIES
    ]
    blockers = _unique(
        blocker
        for mode in modes
        for blocker in mode.blockers
    )
    warnings = _unique(
        warning
        for mode in modes
        for warning in mode.warnings
    )
    ready_modes = sum(1 for mode in modes if mode.ready)
    if blockers and ready_modes:
        state = "degraded"
    elif blockers:
        state = "blocked"
    elif warnings:
        state = "warning"
    else:
        state = "ready"
    evidence = {
        "api_execution_enabled": settings.external_agent_api_execution_enabled,
        "cli_execution_enabled": settings.external_agent_cli_execution_enabled,
        "workspace_configured": bool(settings.voiceops_workspace),
        "preview_first_policy": "required_for_patch",
        "tokens_returned_to_browser": False,
    }
    if credential is not None:
        evidence.update(
            {
                "credential_status": ExternalAgentCredentialStatus.CONNECTED.value,
                "auth_method": credential.auth_method.value,
            }
        )
        if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI:
            cli_command = _credential_cli_command(provider, credential)
            cli_status = _cli_status(cli_command)
            evidence.update(
                {
                    "cli_command": cli_command,
                    "cli_available": cli_status["available"],
                    "cli_path": cli_status["path"],
                    "local_cli_runtime": "isolated_temp_workspace",
                    "env_policy": "minimal_external_agent_env",
                    "safe_env_keys": SAFE_LOCAL_CLI_ENV_KEYS,
                }
            )
        else:
            evidence.update(
                {
                    "credential_storage": "encrypted_server_side",
                    "token_preview_only": True,
                }
            )
    else:
        evidence["credential_status"] = ExternalAgentCredentialStatus.DISCONNECTED.value

    return ExternalAgentProviderPreflight(
        provider=provider,
        label=PROVIDER_LABELS[provider],
        connected=credential is not None,
        status=(
            ExternalAgentCredentialStatus.CONNECTED
            if credential is not None
            else ExternalAgentCredentialStatus.DISCONNECTED
        ),
        state=state,
        summary=_preflight_summary(state, ready_modes, len(modes), blockers, warnings),
        auth_method=credential.auth_method if credential is not None else None,
        account_label=credential.account_label if credential is not None else None,
        blockers=blockers,
        warnings=warnings,
        modes=modes,
        evidence=evidence,
    )


def _preflight_mode(mode: ExternalAgentRunMode, readiness: dict) -> ExternalAgentPreflightMode:
    ready = bool(readiness.get("ready"))
    severity = str(readiness.get("severity") or ("ok" if ready else "warning"))
    detail = str(readiness.get("detail") or "No readiness detail returned.")
    blockers = [] if ready else [detail]
    warnings = [detail] if ready and severity == "warning" else []
    evidence_keys = {
        "auth_method",
        "credential_status",
        "cli_execution_enabled",
        "api_execution_enabled",
        "cli_command",
        "cli_available",
        "cli_path",
        "model_required",
    }
    return ExternalAgentPreflightMode(
        mode=mode,
        ready=ready,
        severity=severity,
        reason=str(readiness.get("reason") or "unknown"),
        detail=detail,
        execution_mode=readiness.get("execution_mode"),
        preview_first=bool(readiness.get("preview_first")),
        blockers=blockers,
        warnings=warnings,
        evidence={key: readiness.get(key) for key in sorted(evidence_keys) if key in readiness},
    )


def _preflight_summary(
    state: str,
    ready_modes: int,
    total_modes: int,
    blockers: list[str],
    warnings: list[str],
) -> str:
    if state == "ready":
        return f"Ready for {ready_modes}/{total_modes} modes."
    if state == "warning":
        return f"Ready with {len(warnings)} warning{'s' if len(warnings) != 1 else ''}."
    if state == "degraded":
        return f"{ready_modes}/{total_modes} modes ready; {len(blockers)} blocker{'s' if len(blockers) != 1 else ''}."
    return f"Blocked; {len(blockers)} setup item{'s' if len(blockers) != 1 else ''} required."


def _unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _provider_mode_readiness(
    settings: Settings,
    provider: ExternalAgentProvider,
    credential: ExternalAgentCredentialRecord | None,
) -> dict[str, dict]:
    if credential is None:
        return {
            mode.value: _readiness(
                False,
                "not_connected",
                "Connect this provider before assigning work.",
                credential_status=ExternalAgentCredentialStatus.DISCONNECTED,
                model_required=True,
            )
            for mode in PROVIDER_CAPABILITIES
        }
    cli_command = _credential_cli_command(provider, credential)
    cli_status = _cli_status(cli_command)
    common = {
        "auth_method": credential.auth_method,
        "credential_status": ExternalAgentCredentialStatus.CONNECTED,
        "cli_execution_enabled": settings.external_agent_cli_execution_enabled,
        "api_execution_enabled": settings.external_agent_api_execution_enabled,
        "cli_command": cli_command if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI else None,
        "cli_available": cli_status["available"] if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI else None,
        "cli_path": cli_status["path"] if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI else None,
        "model_required": True,
    }
    read_only = _read_only_mode_readiness(settings, provider, credential, cli_status, common)
    patch_ready = _patch_mode_readiness(settings, provider, credential, cli_status, common)
    return {
        ExternalAgentRunMode.EXPLAIN.value: read_only,
        ExternalAgentRunMode.REVIEW.value: read_only,
        ExternalAgentRunMode.TEST.value: read_only,
        ExternalAgentRunMode.PATCH.value: patch_ready,
    }


def _read_only_mode_readiness(
    settings: Settings,
    provider: ExternalAgentProvider,
    credential: ExternalAgentCredentialRecord,
    cli_status: dict,
    common: dict,
) -> dict:
    if credential.auth_method in {ExternalAgentAuthMethod.API_KEY, ExternalAgentAuthMethod.OAUTH}:
        if provider == ExternalAgentProvider.CURSOR:
            return _readiness(
                True,
                "mock_adapter",
                "Cursor API execution is not implemented yet; read-only runs use the deterministic adapter.",
                severity="warning",
                execution_mode="mock_adapter",
                **common,
            )
        if settings.external_agent_api_execution_enabled:
            execution_mode = "anthropic_messages_api" if provider == ExternalAgentProvider.CLAUDE else "openai_responses_api"
            return _readiness(
                True,
                "api_ready",
                f"Read-only runs use {execution_mode}; provider tokens stay server-side.",
                execution_mode=execution_mode,
                **common,
            )
        return _readiness(
            True,
            "mock_adapter",
            "API credential is connected, but API execution is disabled; read-only runs use the deterministic adapter.",
            severity="warning",
            execution_mode="mock_adapter",
            **common,
        )

    if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI:
        if not settings.external_agent_cli_execution_enabled:
            return _readiness(
                True,
                "mock_adapter",
                "Local CLI credential is connected, but CLI execution is disabled; read-only runs use the deterministic adapter.",
                severity="warning",
                execution_mode="mock_adapter",
                **common,
            )
        if cli_status["available"] is False:
            return _readiness(
                False,
                "cli_missing",
                f"Local CLI command is not installed or not on PATH: {cli_status['executable']}.",
                severity="error",
                execution_mode="local_cli",
                **common,
            )
        return _readiness(
            True,
            "cli_ready" if cli_status["available"] else "cli_not_verifiable",
            (
                "Read-only runs use the configured local CLI."
                if cli_status["available"]
                else "Read-only runs use the configured local CLI; relative command availability is verified at run time."
            ),
            severity="ok" if cli_status["available"] else "warning",
            execution_mode="local_cli",
            **common,
        )

    return _readiness(
        False,
        "unsupported_auth_method",
        "This credential type is not supported for external agent execution.",
        severity="error",
        **common,
    )


def _patch_mode_readiness(
    settings: Settings,
    provider: ExternalAgentProvider,
    credential: ExternalAgentCredentialRecord,
    cli_status: dict,
    common: dict,
) -> dict:
    if not settings.voiceops_workspace:
        return _readiness(
            False,
            "workspace_missing",
            "VOICEOPS_WORKSPACE is required before an external patch can be proposed.",
            severity="error",
            execution_mode="local_cli_patch" if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI else None,
            preview_first=True,
            **common,
        )
    if credential.auth_method in {ExternalAgentAuthMethod.API_KEY, ExternalAgentAuthMethod.OAUTH}:
        return _readiness(
            False,
            "local_cli_required",
            "Patch mode requires a local CLI credential so the coding agent can prepare a diff in an isolated temp workspace.",
            severity="warning",
            execution_mode="local_cli_patch",
            preview_first=True,
            **common,
        )
    workspace = Path(settings.voiceops_workspace).expanduser()
    if not workspace.exists() or not workspace.is_dir():
        return _readiness(
            False,
            "workspace_unavailable",
            "The configured workspace directory is not available.",
            severity="error",
            execution_mode="local_cli_patch" if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI else None,
            preview_first=True,
            **common,
        )
    if not settings.external_agent_cli_execution_enabled and not (workspace / "app.py").exists():
        return _readiness(
            False,
            "patch_target_missing",
            "This local demo patch adapter requires app.py in the configured workspace.",
            severity="error",
            execution_mode="deterministic_demo_patch",
            preview_first=True,
            **common,
        )
    if settings.external_agent_cli_execution_enabled:
        if cli_status["available"] is False:
            return _readiness(
                False,
                "cli_missing",
                f"Local CLI command is not installed or not on PATH: {cli_status['executable']}.",
                severity="error",
                execution_mode="local_cli_patch",
                preview_first=True,
                **common,
            )
        return _readiness(
            True,
            "preview_first_cli" if cli_status["available"] else "cli_not_verifiable",
            (
                "Patch proposal will run the configured local CLI in an isolated temp workspace and still require approval."
                if cli_status["available"]
                else "Patch proposal will run the configured local CLI in an isolated temp workspace; relative command availability is verified at run time."
            ),
            severity="ok" if cli_status["available"] else "warning",
            execution_mode="local_cli_patch",
            preview_first=True,
            **common,
        )
    return _readiness(
        True,
        "deterministic_demo_patch",
        "CLI execution is disabled; patch proposal uses the deterministic local demo adapter and still requires approval.",
        severity="warning",
        execution_mode="deterministic_demo_patch",
        preview_first=True,
        **common,
    )


def _credential_cli_command(provider: ExternalAgentProvider, credential: ExternalAgentCredentialRecord) -> str:
    if credential.auth_method != ExternalAgentAuthMethod.LOCAL_CLI:
        return ""
    command = str(credential.metadata.get("command") or "").strip()
    return command or _default_cli_command(provider)


def _cli_status(command: str) -> dict:
    command = " ".join(command.strip().split())
    if not command:
        return {"available": False, "executable": "", "path": None}
    try:
        executable = shlex.split(command)[0]
    except ValueError:
        return {"available": False, "executable": command, "path": None}
    path = Path(executable).expanduser()
    if path.is_absolute():
        return {"available": path.exists() and path.is_file(), "executable": executable, "path": str(path) if path.exists() else None}
    if executable.startswith("./") or executable.startswith("../"):
        return {"available": None, "executable": executable, "path": None}
    found = shutil.which(executable)
    return {"available": bool(found), "executable": executable, "path": found}


def _readiness(
    ready: bool,
    reason: str,
    detail: str,
    *,
    severity: str | None = None,
    **evidence,
) -> dict:
    return {
        "ready": ready,
        "reason": reason,
        "detail": detail,
        "severity": severity or ("ok" if ready else "warning"),
        **evidence,
    }


def _oauth_authorize_url(settings: Settings, provider: ExternalAgentProvider) -> str:
    return getattr(settings, f"{provider.value}_oauth_authorize_url")


def _oauth_redirect_uri(settings: Settings, provider: ExternalAgentProvider, override: str | None = None) -> str:
    uri = (override or f"{settings.external_agent_oauth_redirect_base_url.rstrip('/')}/external-agents/oauth/callback").strip()
    if "provider=" in uri:
        return uri
    separator = "&" if "?" in uri else "?"
    return f"{uri}{separator}provider={provider.value}"


def _oauth_token_url(settings: Settings, provider: ExternalAgentProvider) -> str | None:
    return getattr(settings, f"{provider.value}_oauth_token_url")


def _oauth_client_id(settings: Settings, provider: ExternalAgentProvider) -> str | None:
    return getattr(settings, f"{provider.value}_oauth_client_id")


def _oauth_client_secret(settings: Settings, provider: ExternalAgentProvider) -> str | None:
    return getattr(settings, f"{provider.value}_oauth_client_secret")


async def _exchange_oauth_token(
    settings: Settings,
    provider: ExternalAgentProvider,
    code: str,
    redirect_uri: str,
) -> dict:
    token_url = _oauth_token_url(settings, provider)
    client_id = _oauth_client_id(settings, provider)
    client_secret = _oauth_client_secret(settings, provider)
    if not token_url or not client_id or not client_secret:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{provider.value} OAuth is not fully configured")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{provider.value} OAuth token exchange failed") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{provider.value} OAuth token exchange returned {response.status_code}")
    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{provider.value} OAuth token response did not include access_token")
    return {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "exchange_mode": "configured",
    }


def _patch_app_py(before: str) -> str:
    if '@app.get("/health")' in before:
        return before
    suffix = '\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'
    return before.rstrip() + suffix
