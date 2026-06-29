from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, status

from app.auth.models import UserPublic
from app.config import Settings, get_settings
from app.external_agents.security import decrypt_payload, encrypt_payload, preview_secret
from app.llm.connections_models import (
    LLMApiKeyConnectRequest,
    LLMConnectionProvider,
    LLMConnectionProviderInfo,
    LLMConnectionPublic,
    LLMConnectionRecord,
    LLMConnectionStatus,
    LLMProviderPreflightRequest,
    LLMProviderPreflightResponse,
)
from app.llm.connections_store import LLMConnectionStore
from app.llm.models import LLMMessage, LLMRequest
from app.llm.service import get_llm_runtime


PROVIDER_LABELS = {
    LLMConnectionProvider.OPENAI: "OpenAI API",
    LLMConnectionProvider.ANTHROPIC: "Anthropic Claude",
    LLMConnectionProvider.OPENAI_COMPATIBLE: "OpenAI-compatible / GLM",
}


class LLMConnectionService:
    def __init__(self, store: LLMConnectionStore, settings: Settings, runtime_factory=None) -> None:
        self._store = store
        self._settings = settings
        self._runtime_factory = runtime_factory or get_llm_runtime

    def list_providers(self, user: UserPublic) -> list[LLMConnectionProviderInfo]:
        records = {record.provider: record for record in self._store.list_for_user(user.id)}
        return [
            self._provider_info(provider, records.get(provider))
            for provider in LLMConnectionProvider
        ]

    def connect_api_key(
        self,
        provider: LLMConnectionProvider,
        user: UserPublic,
        body: LLMApiKeyConnectRequest,
    ) -> LLMConnectionPublic:
        if provider not in PROVIDER_LABELS:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM provider is not supported: {provider.value}")
        api_key = body.api_key.strip()
        if provider == LLMConnectionProvider.OPENAI and len(api_key) < 8:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="OpenAI API key is required")
        if provider == LLMConnectionProvider.ANTHROPIC and len(api_key) < 8:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Anthropic API key is required")
        if provider == LLMConnectionProvider.OPENAI_COMPATIBLE and not body.base_url:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="OpenAI-compatible base_url is required")
        model = body.model or (
            self._settings.openai_model
            if provider == LLMConnectionProvider.OPENAI
            else self._settings.anthropic_model
            if provider == LLMConnectionProvider.ANTHROPIC
            else self._settings.openai_compatible_model
        )
        now = _now()
        encrypted = encrypt_payload(
            self._settings,
            {
                "auth_method": "api_key",
                "api_key": api_key or "local",
                "model": model,
                "base_url": body.base_url,
            },
        )
        record = LLMConnectionRecord(
            id=f"llm-{uuid4().hex[:12]}",
            user_id=user.id,
            provider=provider,
            account_label=body.account_label or PROVIDER_LABELS[provider],
            model=model,
            base_url=body.base_url,
            encrypted_payload=encrypted,
            token_preview=preview_secret(api_key) if api_key else None,
            created_at=now,
            updated_at=now,
            metadata={"source": "api_key"},
        )
        return _public(self._store.upsert(record))

    def disconnect(self, provider: LLMConnectionProvider, user: UserPublic) -> bool:
        return self._store.delete(user.id, provider.value)

    def settings_for_user(self, user: UserPublic, base_settings: Settings | None = None) -> Settings:
        base = base_settings or self._settings
        records = sorted(self._store.list_for_user(user.id), key=lambda record: record.updated_at, reverse=True)
        for record in records:
            try:
                provider = LLMConnectionProvider(record.provider)
            except ValueError:
                continue
            selected = self.settings_for_user_provider(user, provider, base)
            if _provider_settings_available(provider, selected):
                return selected
        return base

    def settings_for_user_provider(
        self,
        user: UserPublic,
        provider: LLMConnectionProvider,
        base_settings: Settings | None = None,
    ) -> Settings:
        base = base_settings or self._settings
        record = self._store.get(user.id, provider.value)
        if record is None:
            return base
        try:
            payload = decrypt_payload(base, record.encrypted_payload)
        except (InvalidToken, ValueError):
            return base
        api_key = str(payload.get("api_key") or "").strip()
        if provider == LLMConnectionProvider.OPENAI and not api_key:
            return base
        if provider == LLMConnectionProvider.OPENAI:
            return base.model_copy(
                update={
                    "llm_provider": "openai",
                    "openai_api_key": api_key,
                    "openai_model": str(payload.get("model") or record.model or base.openai_model),
                }
            )
        if provider == LLMConnectionProvider.ANTHROPIC:
            return base.model_copy(
                update={
                    "llm_provider": "anthropic",
                    "anthropic_api_key": api_key,
                    "anthropic_model": str(payload.get("model") or record.model or base.anthropic_model),
                    "anthropic_api_base_url": str(payload.get("base_url") or record.base_url or base.anthropic_api_base_url),
                }
            )
        if provider == LLMConnectionProvider.OPENAI_COMPATIBLE:
            base_url = str(payload.get("base_url") or record.base_url or base.openai_compatible_base_url or "").strip()
            if not base_url:
                return base
            return base.model_copy(
                update={
                    "llm_provider": "openai_compatible",
                    "openai_compatible_api_key": api_key or base.openai_compatible_api_key or "local",
                    "openai_compatible_base_url": base_url,
                    "openai_compatible_model": str(payload.get("model") or record.model or base.openai_compatible_model),
                }
            )
        return base

    async def preflight_provider(
        self,
        provider: LLMConnectionProvider,
        user: UserPublic,
        body: LLMProviderPreflightRequest,
    ) -> LLMProviderPreflightResponse:
        settings = self._settings_for_preflight(provider, user, body)
        blockers = _preflight_blockers(provider, settings)
        if blockers:
            return LLMProviderPreflightResponse(
                provider=provider,
                ready=False,
                model=_model_for_provider(provider, settings),
                base_url=_base_url_for_provider(provider, settings),
                blockers=blockers,
                detail="Provider is not configured.",
            )
        started = time.perf_counter()
        text_ok = False
        json_ok = False
        try:
            runtime = self._runtime_factory(settings)
            text_response = await runtime.generate(
                LLMRequest(
                    purpose="provider_preflight",
                    response_format="text",
                    max_tokens=64,
                    messages=[
                        LLMMessage(role="system", content="Return a short readiness response."),
                        LLMMessage(role="user", content="Say ready."),
                    ],
                )
            )
            text_ok = bool(text_response.content.strip())
            if body.require_json:
                json_response = await runtime.generate(
                    LLMRequest(
                        purpose="provider_preflight_json",
                        response_format="json",
                        max_tokens=128,
                        messages=[
                            LLMMessage(role="system", content="Return JSON only."),
                            LLMMessage(role="user", content='Return {"ready": true}.'),
                        ],
                    )
                )
                payload = json.loads(json_response.content)
                json_ok = bool(payload.get("ready"))
            else:
                json_ok = True
        except Exception as exc:
            return LLMProviderPreflightResponse(
                provider=provider,
                ready=False,
                model=_model_for_provider(provider, settings),
                base_url=_base_url_for_provider(provider, settings),
                latency_ms=round((time.perf_counter() - started) * 1000),
                text_ok=text_ok,
                json_ok=json_ok,
                blockers=[exc.__class__.__name__],
                detail=str(exc) or exc.__class__.__name__,
            )
        return LLMProviderPreflightResponse(
            provider=provider,
            ready=text_ok and json_ok,
            model=_model_for_provider(provider, settings),
            base_url=_base_url_for_provider(provider, settings),
            latency_ms=round((time.perf_counter() - started) * 1000),
            text_ok=text_ok,
            json_ok=json_ok,
            blockers=[] if text_ok and json_ok else ["json_response_invalid" if not json_ok else "text_response_empty"],
            detail="Provider preflight passed." if text_ok and json_ok else "Provider responded but failed readiness checks.",
        )

    def _settings_for_preflight(
        self,
        provider: LLMConnectionProvider,
        user: UserPublic,
        body: LLMProviderPreflightRequest,
    ) -> Settings:
        base = self.settings_for_user_provider(user, provider, self._settings)
        if provider == LLMConnectionProvider.OPENAI:
            return base.model_copy(
                update={
                    "llm_provider": "openai",
                    "openai_api_key": body.api_key if body.api_key is not None else base.openai_api_key,
                    "openai_model": body.model or base.openai_model,
                }
            )
        if provider == LLMConnectionProvider.ANTHROPIC:
            return base.model_copy(
                update={
                    "llm_provider": "anthropic",
                    "anthropic_api_key": body.api_key if body.api_key is not None else base.anthropic_api_key,
                    "anthropic_model": body.model or base.anthropic_model,
                    "anthropic_api_base_url": body.base_url or base.anthropic_api_base_url,
                }
            )
        return base.model_copy(
            update={
                "llm_provider": "openai_compatible",
                "openai_compatible_api_key": body.api_key if body.api_key is not None else (base.openai_compatible_api_key or "local"),
                "openai_compatible_base_url": body.base_url or base.openai_compatible_base_url,
                "openai_compatible_model": body.model or base.openai_compatible_model,
            }
        )

    def _provider_info(
        self,
        provider: LLMConnectionProvider,
        record: LLMConnectionRecord | None,
    ) -> LLMConnectionProviderInfo:
        if record:
            return LLMConnectionProviderInfo(
                provider=provider,
                label=PROVIDER_LABELS[provider],
                status=LLMConnectionStatus.CONNECTED,
                connected=True,
                credential_id=record.id,
                account_label=record.account_label,
                model=record.model,
                base_url=record.base_url,
                token_preview=record.token_preview,
                last_connected_at=record.updated_at,
                detail="Connected. This user's AI teammate requests will use this provider.",
            )
        return LLMConnectionProviderInfo(
            provider=provider,
            label=PROVIDER_LABELS[provider],
            status=LLMConnectionStatus.DISCONNECTED,
            connected=False,
            detail="Not connected. VoiceOps will use environment defaults or mock mode.",
        )


@lru_cache
def _service_for_path(path: str) -> LLMConnectionService:
    settings = get_settings()
    return LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)


def get_llm_connection_service(settings: Settings = Depends(get_settings)) -> LLMConnectionService:
    if settings == get_settings():
        return _service_for_path(str(settings.llm_connection_store_path))
    return LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)


def _public(record: LLMConnectionRecord) -> LLMConnectionPublic:
    return LLMConnectionPublic(
        id=record.id,
        provider=record.provider,
        account_label=record.account_label,
        model=record.model,
        base_url=record.base_url,
        token_preview=record.token_preview,
        created_at=record.created_at,
        updated_at=record.updated_at,
        metadata=record.metadata,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _preflight_blockers(provider: LLMConnectionProvider, settings: Settings) -> list[str]:
    if provider == LLMConnectionProvider.OPENAI:
        return [] if settings.openai_api_key else ["missing_openai_api_key"]
    if provider == LLMConnectionProvider.ANTHROPIC:
        return [] if settings.anthropic_api_key else ["missing_anthropic_api_key"]
    if provider == LLMConnectionProvider.OPENAI_COMPATIBLE:
        return [] if settings.openai_compatible_base_url else ["missing_openai_compatible_base_url"]
    return [f"unsupported_provider:{provider.value}"]


def _model_for_provider(provider: LLMConnectionProvider, settings: Settings) -> str | None:
    if provider == LLMConnectionProvider.OPENAI:
        return settings.openai_model
    if provider == LLMConnectionProvider.ANTHROPIC:
        return settings.anthropic_model
    if provider == LLMConnectionProvider.OPENAI_COMPATIBLE:
        return settings.openai_compatible_model
    return None


def _provider_settings_available(provider: LLMConnectionProvider, settings: Settings) -> bool:
    if provider == LLMConnectionProvider.OPENAI:
        return settings.llm_provider == "openai" and bool(settings.openai_api_key)
    if provider == LLMConnectionProvider.ANTHROPIC:
        return settings.llm_provider == "anthropic" and bool(settings.anthropic_api_key)
    if provider == LLMConnectionProvider.OPENAI_COMPATIBLE:
        return settings.llm_provider == "openai_compatible" and bool(settings.openai_compatible_base_url)
    return False


def _base_url_for_provider(provider: LLMConnectionProvider, settings: Settings) -> str | None:
    if provider == LLMConnectionProvider.ANTHROPIC:
        return settings.anthropic_api_base_url
    if provider == LLMConnectionProvider.OPENAI_COMPATIBLE:
        return settings.openai_compatible_base_url
    return None
