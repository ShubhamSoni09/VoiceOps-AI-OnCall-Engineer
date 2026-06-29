from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.models import AgentRole
from app.auth.models import UserPublic
from app.config import Settings
from app.llm.connections_models import LLMConnectionProvider, LLMProviderPreflightRequest
from app.llm.connections_service import LLMConnectionService


AgentLLMProvider = Literal["default", "mock", "openai", "anthropic", "openai_compatible", "bedrock"]


class AgentLLMRoute(BaseModel):
    room_id: str
    role: AgentRole
    provider: AgentLLMProvider = "default"
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    updated_by: str | None = None
    updated_by_name: str | None = None
    updated_at: datetime


class AgentLLMRouteRequest(BaseModel):
    role: AgentRole
    provider: AgentLLMProvider = "default"
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)


class AgentLLMRuntimeRoute(BaseModel):
    room_id: str
    role: AgentRole
    route_provider: AgentLLMProvider
    configured_model: str | None = None
    configured_base_url: str | None = None
    effective_provider: str
    effective_model: str | None = None
    ready: bool
    status: str
    detail: str
    credential_source: str
    warnings: list[str] = Field(default_factory=list)


class AgentLLMRoutePreflightResponse(BaseModel):
    room_id: str
    role: AgentRole
    route_provider: AgentLLMProvider
    effective_provider: str
    effective_model: str | None = None
    ready: bool
    status: str
    detail: str
    credential_source: str
    latency_ms: int | None = None
    text_ok: bool = False
    json_ok: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentLLMRoutingSnapshot(BaseModel):
    room_id: str
    routes: list[AgentLLMRoute]
    runtime: list[AgentLLMRuntimeRoute] = Field(default_factory=list)


class AgentLLMRouteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            os.chmod(self.path, 0o600)

    def list_room(self, room_id: str) -> list[AgentLLMRoute]:
        return [route for route in self._read() if route.room_id == room_id]

    def get(self, room_id: str, role: AgentRole) -> AgentLLMRoute | None:
        for route in self._read():
            if route.room_id == room_id and route.role == role:
                return route
        return None

    def upsert(self, route: AgentLLMRoute) -> AgentLLMRoute:
        routes = self._read()
        for index, existing in enumerate(routes):
            if existing.room_id == route.room_id and existing.role == route.role:
                routes[index] = route
                self._write(routes)
                return route
        routes.append(route)
        self._write(routes)
        return route

    def _read(self) -> list[AgentLLMRoute]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        if isinstance(data, dict):
            data = data.get("routes", [])
        return [AgentLLMRoute.model_validate(item) for item in data]

    def _write(self, routes: list[AgentLLMRoute]) -> None:
        self.path.write_text(json.dumps([route.model_dump(mode="json") for route in routes], indent=2), encoding="utf-8")
        os.chmod(self.path, 0o600)


class AgentLLMRoutingService:
    def __init__(
        self,
        store: AgentLLMRouteStore,
        settings: Settings,
        connections: LLMConnectionService,
    ) -> None:
        self._store = store
        self._settings = settings
        self._connections = connections

    def snapshot(self, room_id: str, user: UserPublic | None = None) -> AgentLLMRoutingSnapshot:
        configured = {route.role: route for route in self._store.list_room(room_id)}
        routes = [
            configured.get(role) or _default_route(room_id, role)
            for role in AgentRole
        ]
        runtime = self.runtime_matrix(room_id, user, routes=routes) if user else []
        return AgentLLMRoutingSnapshot(room_id=room_id, routes=routes, runtime=runtime)

    def update_route(self, room_id: str, user: UserPublic, body: AgentLLMRouteRequest) -> AgentLLMRoute:
        route = AgentLLMRoute(
            room_id=room_id,
            role=body.role,
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            updated_by=user.id,
            updated_by_name=user.name,
            updated_at=_now(),
        )
        return self._store.upsert(route)

    async def preflight_route(
        self,
        room_id: str,
        user: UserPublic,
        body: AgentLLMRouteRequest,
    ) -> AgentLLMRoutePreflightResponse:
        route = AgentLLMRoute(
            room_id=room_id,
            role=body.role,
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            updated_at=_now(),
        )
        selected, metadata = self.settings_for_route(user, route, self._settings)
        readiness = _runtime_readiness(
            selected,
            route,
            {
                info.provider.value: info.connected
                for info in self._connections.list_providers(user)
            },
        )
        if not readiness["ready"]:
            return AgentLLMRoutePreflightResponse(
                room_id=room_id,
                role=body.role,
                route_provider=body.provider,
                effective_provider=metadata["provider"],
                effective_model=metadata["model"],
                text_ok=False,
                json_ok=False,
                blockers=readiness["warnings"] or [readiness["status"]],
                **readiness,
            )
        if selected.llm_provider == "mock":
            return AgentLLMRoutePreflightResponse(
                room_id=room_id,
                role=body.role,
                route_provider=body.provider,
                effective_provider=metadata["provider"],
                effective_model=metadata["model"],
                text_ok=True,
                json_ok=True,
                blockers=[],
                **readiness,
            )
        if selected.llm_provider == "bedrock":
            warnings = ["Bedrock preflight checks configuration only; AWS credential validation runs at execution time."]
            return AgentLLMRoutePreflightResponse(
                room_id=room_id,
                role=body.role,
                route_provider=body.provider,
                effective_provider=metadata["provider"],
                effective_model=metadata["model"],
                text_ok=False,
                json_ok=False,
                blockers=[],
                **{**readiness, "warnings": warnings},
            )
        if selected.llm_provider == "openai":
            provider = LLMConnectionProvider.OPENAI
        elif selected.llm_provider == "anthropic":
            provider = LLMConnectionProvider.ANTHROPIC
        else:
            provider = LLMConnectionProvider.OPENAI_COMPATIBLE
        result = await self._connections.preflight_provider(
            provider,
            user,
            LLMProviderPreflightRequest(
                model=metadata["model"],
                base_url=_selected_base_url(selected),
                require_json=True,
            ),
        )
        return AgentLLMRoutePreflightResponse(
            room_id=room_id,
            role=body.role,
            route_provider=body.provider,
            effective_provider=metadata["provider"],
            effective_model=result.model or metadata["model"],
            ready=result.ready,
            status="ready" if result.ready else "failed",
            detail=result.detail,
            credential_source=readiness["credential_source"],
            latency_ms=result.latency_ms,
            text_ok=result.text_ok,
            json_ok=result.json_ok,
            blockers=result.blockers,
            warnings=readiness["warnings"],
        )

    def runtime_matrix(
        self,
        room_id: str,
        user: UserPublic | None,
        *,
        routes: list[AgentLLMRoute] | None = None,
    ) -> list[AgentLLMRuntimeRoute]:
        if user is None:
            return []
        connection_status = {
            info.provider.value: info.connected
            for info in self._connections.list_providers(user)
        }
        selected_routes = routes or self.snapshot(room_id).routes
        rows: list[AgentLLMRuntimeRoute] = []
        for route in selected_routes:
            selected, metadata = self.settings_for_route(user, route, self._settings)
            readiness = _runtime_readiness(selected, route, connection_status)
            rows.append(
                AgentLLMRuntimeRoute(
                    room_id=room_id,
                    role=route.role,
                    route_provider=route.provider,
                    configured_model=route.model,
                    configured_base_url=route.base_url,
                    effective_provider=metadata["provider"],
                    effective_model=metadata["model"],
                    **readiness,
                )
            )
        return rows

    def settings_for_role(
        self,
        room_id: str,
        user: UserPublic,
        role: AgentRole,
        base_settings: Settings | None = None,
    ) -> tuple[Settings, dict]:
        base = base_settings or self._settings
        route = self._store.get(room_id, role) or _default_route(room_id, role)
        return self.settings_for_route(user, route, base)

    def settings_for_route(
        self,
        user: UserPublic,
        route: AgentLLMRoute,
        base_settings: Settings | None = None,
    ) -> tuple[Settings, dict]:
        base = base_settings or self._settings
        if route.provider == "default":
            selected = self._connections.settings_for_user(user, base)
        elif route.provider == "mock":
            selected = base.model_copy(update={"llm_provider": "mock"})
        elif route.provider == "openai":
            selected = self._connections.settings_for_user_provider(user, LLMConnectionProvider.OPENAI, base)
            selected = selected.model_copy(
                update={
                    "llm_provider": "openai",
                    "openai_model": route.model or selected.openai_model,
                }
            )
        elif route.provider == "anthropic":
            selected = self._connections.settings_for_user_provider(user, LLMConnectionProvider.ANTHROPIC, base)
            selected = selected.model_copy(
                update={
                    "llm_provider": "anthropic",
                    "anthropic_model": route.model or selected.anthropic_model,
                    "anthropic_api_base_url": route.base_url or selected.anthropic_api_base_url,
                }
            )
        elif route.provider == "openai_compatible":
            selected = self._connections.settings_for_user_provider(user, LLMConnectionProvider.OPENAI_COMPATIBLE, base)
            selected = selected.model_copy(
                update={
                    "llm_provider": "openai_compatible",
                    "openai_compatible_api_key": selected.openai_compatible_api_key or "local",
                    "openai_compatible_base_url": route.base_url or selected.openai_compatible_base_url,
                    "openai_compatible_model": route.model or selected.openai_compatible_model,
                }
            )
        else:
            selected = base.model_copy(
                update={
                    "llm_provider": "bedrock",
                    "bedrock_model_id": route.model or base.bedrock_model_id,
                }
        )
        metadata = {
            "role": route.role.value,
            "provider": selected.llm_provider,
            "model": _selected_model(selected),
            "route_provider": route.provider,
            "configured_model": route.model,
            "configured_base_url": route.base_url,
        }
        return selected, metadata


def _default_route(room_id: str, role: AgentRole) -> AgentLLMRoute:
    return AgentLLMRoute(room_id=room_id, role=role, provider="default", updated_at=_now())


def _selected_model(settings: Settings) -> str | None:
    if settings.llm_provider == "openai":
        return settings.openai_model
    if settings.llm_provider == "anthropic":
        return settings.anthropic_model
    if settings.llm_provider == "openai_compatible":
        return settings.openai_compatible_model
    if settings.llm_provider == "bedrock":
        return settings.bedrock_model_id
    if settings.llm_provider == "mock":
        return "mock-deterministic"
    return None


def _selected_base_url(settings: Settings) -> str | None:
    if settings.llm_provider == "anthropic":
        return settings.anthropic_api_base_url
    if settings.llm_provider == "openai_compatible":
        return settings.openai_compatible_base_url
    return None


def _runtime_readiness(
    settings: Settings,
    route: AgentLLMRoute,
    connection_status: dict[str, bool],
) -> dict:
    provider = settings.llm_provider
    if provider == "mock":
        return {
            "ready": True,
            "status": "ready",
            "detail": "Using deterministic local mock runtime.",
            "credential_source": "local_mock",
            "warnings": [],
        }
    if provider == "openai":
        connected = connection_status.get("openai", False)
        if settings.openai_api_key:
            return {
                "ready": True,
                "status": "ready",
                "detail": "OpenAI runtime has an API key from the requester connection or environment.",
                "credential_source": "user_connection" if connected else "environment",
                "warnings": [],
            }
        return {
            "ready": False,
            "status": "missing_credential",
            "detail": "OpenAI route is selected but no API key is available for this requester.",
            "credential_source": "missing",
            "warnings": ["Connect OpenAI or configure OPENAI_API_KEY."],
        }
    if provider == "anthropic":
        connected = connection_status.get("anthropic", False)
        if settings.anthropic_api_key:
            return {
                "ready": True,
                "status": "ready",
                "detail": "Anthropic runtime has an API key from the requester connection or environment.",
                "credential_source": "user_connection" if connected else "environment",
                "warnings": [],
            }
        return {
            "ready": False,
            "status": "missing_credential",
            "detail": "Anthropic route is selected but no API key is available for this requester.",
            "credential_source": "missing",
            "warnings": ["Connect Anthropic or configure ANTHROPIC_API_KEY."],
        }
    if provider == "openai_compatible":
        connected = connection_status.get("openai_compatible", False)
        if settings.openai_compatible_base_url:
            return {
                "ready": True,
                "status": "ready",
                "detail": "OpenAI-compatible runtime has a base URL and model configured.",
                "credential_source": "user_connection" if connected else "route_or_environment",
                "warnings": [],
            }
        return {
            "ready": False,
            "status": "missing_base_url",
            "detail": "OpenAI-compatible route needs a base URL for GLM/local inference.",
            "credential_source": "missing",
            "warnings": ["Set a route base URL or connect an OpenAI-compatible provider."],
        }
    if provider == "bedrock":
        ready = bool(settings.bedrock_model_id and settings.bedrock_region)
        return {
            "ready": ready,
            "status": "configured" if ready else "missing_configuration",
            "detail": "Bedrock uses environment AWS credentials; this dashboard does not expose secrets.",
            "credential_source": "environment",
            "warnings": [] if ready else ["Configure BEDROCK_MODEL_ID and BEDROCK_REGION."],
        }
    return {
        "ready": False,
        "status": "unsupported_provider",
        "detail": f"Unsupported effective LLM provider: {provider}",
        "credential_source": "unknown",
        "warnings": [f"Route provider {route.provider} resolved to unsupported provider {provider}."],
    }


def _now() -> datetime:
    return datetime.now(UTC)
