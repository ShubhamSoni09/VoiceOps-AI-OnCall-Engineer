from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service
from app.external_agents.models import (
    ExternalAgentApiKeyConnectRequest,
    ExternalAgentCapabilityReport,
    ExternalAgentCredentialPublic,
    ExternalAgentLocalCliConnectRequest,
    ExternalAgentOAuthCallbackRequest,
    ExternalAgentOAuthStartRequest,
    ExternalAgentOAuthStartResponse,
    ExternalAgentPreflightReport,
    ExternalAgentProvider,
    ExternalAgentProviderInfo,
    ExternalAgentRecommendationRequest,
    ExternalAgentRecommendationResponse,
    ExternalAgentRunRequest,
    ExternalAgentRunResponse,
    ExternalAgentSetupGuideReport,
)
from app.external_agents.service import ExternalAgentService, get_external_agent_service

router = APIRouter(prefix="/external-agents", tags=["external-agents"])


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before running external agents.")


@router.get("/providers", response_model=list[ExternalAgentProviderInfo])
async def list_external_agent_providers(
    user: UserPublic = Depends(get_current_user),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> list[ExternalAgentProviderInfo]:
    return service.list_providers(user)


@router.get("/preflight", response_model=ExternalAgentPreflightReport)
async def get_external_agent_preflight(
    user: UserPublic = Depends(get_current_user),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentPreflightReport:
    return service.preflight_report(user)


@router.get("/setup-guide", response_model=ExternalAgentSetupGuideReport)
async def get_external_agent_setup_guide(
    user: UserPublic = Depends(get_current_user),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentSetupGuideReport:
    return service.setup_guide_report(user)


@router.get("/capabilities", response_model=ExternalAgentCapabilityReport)
async def get_external_agent_capabilities(
    user: UserPublic = Depends(get_current_user),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentCapabilityReport:
    return service.capability_report(user)


@router.post("/recommendations", response_model=ExternalAgentRecommendationResponse)
async def recommend_external_agent(
    body: ExternalAgentRecommendationRequest,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentRecommendationResponse:
    return service.recommend(user, body)


@router.post("/providers/{provider}/credentials/api-key", response_model=ExternalAgentCredentialPublic)
async def connect_external_agent_api_key(
    provider: ExternalAgentProvider,
    body: ExternalAgentApiKeyConnectRequest,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentCredentialPublic:
    return service.connect_api_key(provider, user, body)


@router.post("/providers/{provider}/credentials/local-cli", response_model=ExternalAgentCredentialPublic)
async def connect_external_agent_local_cli(
    provider: ExternalAgentProvider,
    body: ExternalAgentLocalCliConnectRequest,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentCredentialPublic:
    return service.connect_local_cli(provider, user, body)


@router.post("/providers/{provider}/oauth/start", response_model=ExternalAgentOAuthStartResponse)
async def start_external_agent_oauth(
    provider: ExternalAgentProvider,
    body: ExternalAgentOAuthStartRequest,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentOAuthStartResponse:
    return service.start_oauth(provider, user, body)


@router.post("/oauth/callback", response_model=ExternalAgentCredentialPublic)
async def complete_external_agent_oauth(
    body: ExternalAgentOAuthCallbackRequest,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> ExternalAgentCredentialPublic:
    return await service.complete_oauth(user, body)


@router.get("/oauth/callback")
async def complete_external_agent_oauth_redirect(
    provider: ExternalAgentProvider,
    code: str = Query(default=""),
    state: str = Query(default=""),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> HTMLResponse:
    if not code or not state:
        return HTMLResponse(
            f"<h1>{html.escape(provider.value)} connection failed</h1><p>OAuth code and state are required.</p>",
            status_code=400,
        )
    try:
        credential = await service.complete_oauth_redirect(provider, code=code, state=state)
    except HTTPException as exc:
        return HTMLResponse(
            f"<h1>{html.escape(provider.value)} connection failed</h1><p>{html.escape(str(exc.detail))}</p>",
            status_code=exc.status_code,
        )
    account = html.escape(credential.account_label or provider.value)
    return HTMLResponse(f"<h1>{html.escape(provider.value)} connected</h1><p>{account} is ready. You can close this window.</p>")


@router.delete("/providers/{provider}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_external_agent(
    provider: ExternalAgentProvider,
    user: UserPublic = Depends(require_permission("credentials:manage_own")),
    service: ExternalAgentService = Depends(get_external_agent_service),
) -> Response:
    service.disconnect(provider, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rooms/{room_id}/runs", response_model=ExternalAgentRunResponse)
async def run_external_agent(
    room_id: str,
    body: ExternalAgentRunRequest,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: ExternalAgentService = Depends(get_external_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> ExternalAgentRunResponse:
    _ensure_room_access(room_id, user, collab)
    result = service.run(room_id, user, body)
    await events.publish(
        room_id,
        "external_agent_run_updated",
        actor_id=user.id,
        payload={
            "provider": result.provider.value,
            "provider_run_id": result.provider_run_id,
            "action_id": result.action_id,
            "status": result.status,
        },
    )
    return result
