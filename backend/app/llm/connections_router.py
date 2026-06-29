from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.auth.dependencies import require_permission
from app.auth.models import UserPublic
from app.llm.connections_models import (
    LLMApiKeyConnectRequest,
    LLMConnectionProvider,
    LLMConnectionProviderInfo,
    LLMConnectionPublic,
    LLMProviderPreflightRequest,
    LLMProviderPreflightResponse,
)
from app.llm.connections_service import LLMConnectionService, get_llm_connection_service


router = APIRouter(prefix="/llm", tags=["llm-connections"])


@router.get("/providers", response_model=list[LLMConnectionProviderInfo])
async def list_llm_providers(
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: LLMConnectionService = Depends(get_llm_connection_service),
) -> list[LLMConnectionProviderInfo]:
    return service.list_providers(user)


@router.post("/providers/{provider}/credentials/api-key", response_model=LLMConnectionPublic)
async def connect_llm_api_key(
    provider: LLMConnectionProvider,
    body: LLMApiKeyConnectRequest,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: LLMConnectionService = Depends(get_llm_connection_service),
) -> LLMConnectionPublic:
    return service.connect_api_key(provider, user, body)


@router.post("/providers/{provider}/preflight", response_model=LLMProviderPreflightResponse)
async def preflight_llm_provider(
    provider: LLMConnectionProvider,
    body: LLMProviderPreflightRequest | None = None,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: LLMConnectionService = Depends(get_llm_connection_service),
) -> LLMProviderPreflightResponse:
    return await service.preflight_provider(provider, user, body or LLMProviderPreflightRequest())


@router.delete("/providers/{provider}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_llm_provider(
    provider: LLMConnectionProvider,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: LLMConnectionService = Depends(get_llm_connection_service),
) -> Response:
    service.disconnect(provider, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
