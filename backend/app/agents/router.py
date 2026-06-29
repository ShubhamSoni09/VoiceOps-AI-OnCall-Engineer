from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents.models import (
    AgentAssignment,
    AgentAssignmentCancelRequest,
    AgentAssignmentClearResponse,
    AgentAssignmentListResponse,
    AgentAssignmentRequest,
    AgentAssignmentStatusUpdate,
    AgentRun,
    AgentRunCancelRequest,
    AgentRunListResponse,
    AgentRunRequest,
)
from app.agents.llm_routing import (
    AgentLLMRoute,
    AgentLLMRoutePreflightResponse,
    AgentLLMRouteRequest,
    AgentLLMRoutingSnapshot,
)
from app.agents.service import MultiAgentService, get_multi_agent_service
from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.service import CollaborationService, get_collaboration_service

router = APIRouter(prefix="/agents", tags=["agents"])


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before accessing its agent data.")


@router.get("/rooms/{room_id}/llm-routing", response_model=AgentLLMRoutingSnapshot)
async def get_agent_llm_routing(
    room_id: str,
    user: UserPublic = Depends(get_current_user),
    service: MultiAgentService = Depends(get_multi_agent_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentLLMRoutingSnapshot:
    _ensure_room_access(room_id, user, collab)
    return service.llm_routing(room_id, user)


@router.put("/rooms/{room_id}/llm-routing", response_model=AgentLLMRoute)
async def update_agent_llm_route(
    room_id: str,
    body: AgentLLMRouteRequest,
    user: UserPublic = Depends(require_permission("agent:approve")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentLLMRoute:
    _ensure_room_access(room_id, user, collab)
    route = service.update_llm_route(room_id, user, body)
    await events.publish(
        room_id,
        "agent_llm_route_updated",
        actor_id=user.id,
        payload={"role": route.role.value, "provider": route.provider, "model": route.model},
    )
    return route


@router.post("/rooms/{room_id}/llm-routing/preflight", response_model=AgentLLMRoutePreflightResponse)
async def preflight_agent_llm_route(
    room_id: str,
    body: AgentLLMRouteRequest,
    user: UserPublic = Depends(require_permission("agent:approve")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentLLMRoutePreflightResponse:
    _ensure_room_access(room_id, user, collab)
    return await service.preflight_llm_route(room_id, user, body)


@router.get("/rooms/{room_id}/runs", response_model=AgentRunListResponse)
async def list_agent_runs(
    room_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    user: UserPublic = Depends(get_current_user),
    service: MultiAgentService = Depends(get_multi_agent_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentRunListResponse:
    _ensure_room_access(room_id, user, collab)
    return AgentRunListResponse(runs=service.list_runs(room_id, limit=limit))


@router.get("/rooms/{room_id}/runs/{run_id}", response_model=AgentRun)
async def get_agent_run(
    room_id: str,
    run_id: str,
    user: UserPublic = Depends(get_current_user),
    service: MultiAgentService = Depends(get_multi_agent_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentRun:
    _ensure_room_access(room_id, user, collab)
    run = service.get_run(room_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.get("/rooms/{room_id}/assignments", response_model=AgentAssignmentListResponse)
async def list_agent_assignments(
    room_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    user: UserPublic = Depends(get_current_user),
    service: MultiAgentService = Depends(get_multi_agent_service),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignmentListResponse:
    _ensure_room_access(room_id, user, collab)
    return AgentAssignmentListResponse(assignments=service.list_assignments(room_id, limit=limit))


@router.post("/rooms/{room_id}/assignments", response_model=AgentAssignment)
async def create_agent_assignment(
    room_id: str,
    body: AgentAssignmentRequest,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignment:
    _ensure_room_access(room_id, user, collab)
    assignment = service.create_assignment(room_id, user, body)
    await events.publish(room_id, "agent_assignment_updated", actor_id=user.id, payload={"assignment_id": assignment.id})
    return assignment


@router.patch("/rooms/{room_id}/assignments/{assignment_id}", response_model=AgentAssignment)
async def update_agent_assignment(
    room_id: str,
    assignment_id: str,
    body: AgentAssignmentStatusUpdate,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignment:
    _ensure_room_access(room_id, user, collab)
    assignment = service.update_assignment_status(room_id, assignment_id, user, body)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Agent assignment not found")
    await events.publish(room_id, "agent_assignment_updated", actor_id=user.id, payload={"assignment_id": assignment.id})
    return assignment


@router.post("/rooms/{room_id}/assignments/clear-completed", response_model=AgentAssignmentClearResponse)
async def clear_completed_agent_assignments(
    room_id: str,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignmentClearResponse:
    _ensure_room_access(room_id, user, collab)
    cleared_count, assignments = service.clear_completed_assignments(room_id, user)
    await events.publish(room_id, "agent_assignment_updated", actor_id=user.id, payload={"cleared_count": cleared_count})
    return AgentAssignmentClearResponse(cleared_count=cleared_count, assignments=assignments)


@router.post("/rooms/{room_id}/assignments/{assignment_id}/cancel", response_model=AgentAssignment)
async def cancel_agent_assignment(
    room_id: str,
    assignment_id: str,
    body: AgentAssignmentCancelRequest | None = None,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignment:
    _ensure_room_access(room_id, user, collab)
    try:
        assignment = await service.cancel_assignment(
            room_id,
            assignment_id,
            user,
            body or AgentAssignmentCancelRequest(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if assignment is None:
        raise HTTPException(status_code=404, detail="Agent assignment not found")
    await events.publish(room_id, "agent_assignment_updated", actor_id=user.id, payload={"assignment_id": assignment.id})
    return assignment


@router.post("/rooms/{room_id}/assignments/{assignment_id}/retry", response_model=AgentAssignment)
async def retry_agent_assignment(
    room_id: str,
    assignment_id: str,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignment:
    _ensure_room_access(room_id, user, collab)
    try:
        assignment = service.retry_assignment(room_id, assignment_id, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if assignment is None:
        raise HTTPException(status_code=404, detail="Agent assignment not found")
    await events.publish(room_id, "agent_assignment_updated", actor_id=user.id, payload={"assignment_id": assignment.id})
    return assignment


@router.post("/rooms/{room_id}/assignments/{assignment_id}/dispatch", response_model=AgentAssignment)
async def dispatch_agent_assignment(
    room_id: str,
    assignment_id: str,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentAssignment:
    _ensure_room_access(room_id, user, collab)
    try:
        assignment = await service.dispatch_assignment(room_id, assignment_id, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if assignment is None:
        raise HTTPException(status_code=404, detail="Agent assignment not found")
    await events.publish(room_id, "agent_assignment_updated", actor_id=user.id, payload={"assignment_id": assignment.id})
    return assignment


@router.post("/rooms/{room_id}/runs", response_model=AgentRun)
async def start_agent_run(
    room_id: str,
    body: AgentRunRequest,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentRun:
    _ensure_room_access(room_id, user, collab)
    run = await service.start_run(room_id, user, body)
    await events.publish(room_id, "agent_run_updated", actor_id=user.id, payload={"run_id": run.id})
    return run


@router.post("/rooms/{room_id}/runs/{run_id}/cancel", response_model=AgentRun)
async def cancel_agent_run(
    room_id: str,
    run_id: str,
    body: AgentRunCancelRequest | None = None,
    user: UserPublic = Depends(require_permission("agent:run")),
    service: MultiAgentService = Depends(get_multi_agent_service),
    events: RoomEventHub = Depends(get_room_event_hub),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> AgentRun:
    _ensure_room_access(room_id, user, collab)
    run = await service.cancel_run(
        room_id,
        run_id,
        user,
        reason=body.reason if body else "cancelled by user",
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    await events.publish(room_id, "agent_run_updated", actor_id=user.id, payload={"run_id": run.id})
    return run
