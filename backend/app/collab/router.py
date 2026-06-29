import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.auth.dependencies import get_current_user, get_user_store, require_permission
from app.auth.models import UserPublic
from app.auth.security import decode_access_token, to_public_user
from app.collab.events import RoomEventHub, get_room_event_hub
from app.collab.models import (
    ActionCommitRequest,
    ActionDecisionRequest,
    ActionPullRequestRequest,
    ActionPullRequestResponse,
    AgentAction,
    AgentSettings,
    AgentSettingsUpdate,
    AuditEvent,
    AuditQueryRequest,
    AuditQueryResponse,
    CommandRouteRequest,
    CommandRouteResponse,
    HandoffSummary,
    JoinRoomRequest,
    LeaveRoomResponse,
    MemoryHealthResponse,
    MemoryItem,
    MemoryKind,
    MemoryQueryRequest,
    MemoryQueryResponse,
    ProvenanceQueryRequest,
    ProvenanceQueryResponse,
    RagQueryRequest,
    RagQueryResponse,
    RagIndexResponse,
    RoomSnapshot,
    RoomTraceResponse,
    RoomWorkspaceUpdate,
    TextMessageRequest,
    TimelineMessage,
    WorkDashboardMetric,
    WorkDashboardReadiness,
    WorkDashboardSnapshot,
)
from app.collab.service import CollaborationService, get_collaboration_service
from app.console.router import (
    WorkspaceCloneRequest,
    _ensure_clone_target_inside_root,
    _github_repo_name,
    _is_github_clone_url,
)
from app.config import Settings, get_settings
from app.rag.providers import EmbeddingProviderError
from app.redaction import redact_sensitive_text
from app.voice_agent.meeting_router import MeetingCommandRouter
from app.workspace.github import clone_github_repo
from app.workspace.git import WorkspaceGitService
from app.workspace.models import (
    CodeQueryRequest,
    CodeQueryResponse,
    GitDiffResponse,
    GitStatusResponse,
    WorkspaceTreeResponse,
)
from app.workspace.service import WorkspaceCodeService
from app.workspace.tools import WorkspaceError

router = APIRouter(prefix="/collab", tags=["collaboration"])


def _ensure_room_access(room_id: str, user: UserPublic, service: CollaborationService) -> None:
    if service.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before accessing its collaboration data.")


@router.get("/agent-settings", response_model=AgentSettings)
async def agent_settings(
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> AgentSettings:
    return service.agent_settings()


@router.put("/rooms/{room_id}/agent-settings", response_model=AgentSettings)
async def update_agent_settings(
    room_id: str,
    body: AgentSettingsUpdate,
    _user: UserPublic = Depends(require_permission("admin:manage")),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> AgentSettings:
    _ensure_room_access(room_id, _user, service)
    try:
        settings = service.update_agent_settings(body, room_id=room_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await events.publish(room_id, "agent_settings_updated", actor_id=_user.id)
    return settings


@router.post("/rooms/{room_id}/join", response_model=RoomSnapshot)
async def join_room(
    room_id: str,
    body: JoinRoomRequest,
    user: UserPublic = Depends(get_current_user),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> RoomSnapshot:
    try:
        snapshot = service.join_room(room_id, user, body)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await events.publish(room_id, "participant_joined", actor_id=user.id)
    return snapshot


@router.put("/rooms/{room_id}/workspace", response_model=RoomSnapshot)
async def update_room_workspace(
    room_id: str,
    body: RoomWorkspaceUpdate,
    user: UserPublic = Depends(require_permission("admin:manage")),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> RoomSnapshot:
    _ensure_room_access(room_id, user, service)
    try:
        snapshot = service.update_room_workspace(room_id, body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await events.publish(room_id, "room_workspace_updated", actor_id=user.id)
    return snapshot


@router.post("/rooms/{room_id}/workspace/clone", response_model=RoomSnapshot)
async def clone_room_workspace(
    room_id: str,
    body: WorkspaceCloneRequest,
    user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> RoomSnapshot:
    _ensure_room_access(room_id, user, service)
    remote_url = body.remote_url.strip()
    if not _is_github_clone_url(remote_url):
        raise HTTPException(status_code=400, detail="Clone URL must be a GitHub HTTPS or SSH repository URL.")
    target = _room_clone_target(room_id, body.target_path, remote_url, settings)
    if target.exists() and any(target.iterdir()):
        raise HTTPException(status_code=409, detail="Target path already exists and is not empty.")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(clone_github_repo, remote_url, target)
    if result.returncode != 0:
        detail = redact_sensitive_text(result.stderr or result.stdout or "git clone failed").strip()[-1000:]
        raise HTTPException(status_code=409, detail=detail)
    try:
        snapshot = service.update_room_workspace(room_id, str(target.resolve()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await events.publish(room_id, "room_workspace_updated", actor_id=user.id)
    return snapshot


@router.post("/rooms/{room_id}/leave", response_model=LeaveRoomResponse)
async def leave_room(
    room_id: str,
    user: UserPublic = Depends(get_current_user),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> LeaveRoomResponse:
    _ensure_room_access(room_id, user, service)
    participant = service.leave_room(room_id, user)
    await events.publish(room_id, "participant_left", actor_id=user.id)
    return LeaveRoomResponse(status="left", participant=participant)


@router.get("/rooms/{room_id}", response_model=RoomSnapshot)
async def room_snapshot(
    room_id: str,
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> RoomSnapshot:
    _ensure_room_access(room_id, _user, service)
    return service.snapshot(room_id)


def get_dashboard_multi_agent_service() -> Any:
    from app.agents.service import get_multi_agent_service

    return get_multi_agent_service()


@router.get("/rooms/{room_id}/work-dashboard", response_model=WorkDashboardSnapshot)
async def room_work_dashboard(
    room_id: str,
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
    agents: Any = Depends(get_dashboard_multi_agent_service),
) -> WorkDashboardSnapshot:
    _ensure_room_access(room_id, _user, service)
    snapshot = service.work_dashboard(room_id)
    assignments = agents.list_assignments(room_id, limit=100)
    queue_health = _queue_health(assignments, snapshot)
    return snapshot.model_copy(
        update={
            "queue_health": queue_health,
            "readiness": _dashboard_readiness(snapshot, queue_health),
        }
    )


def _queue_health(assignments: list[Any], snapshot: WorkDashboardSnapshot) -> list[WorkDashboardMetric]:
    terminal = {"completed", "failed", "cancelled"}
    active = [assignment for assignment in assignments if _status_value(assignment.status) not in terminal]
    stale = [assignment for assignment in active if _assignment_age_minutes(assignment) >= 30]
    blocked = [
        assignment for assignment in assignments
        if assignment.metadata.get("readiness") and not assignment.metadata["readiness"].get("ready", True)
    ]
    pending_approvals = len(snapshot.approvals)
    return [
        WorkDashboardMetric(
            label="Open",
            value=len(active),
            tone="info" if active else "neutral",
            detail=f"{len(active)} assignment{' is' if len(active) == 1 else 's are'} still open.",
        ),
        WorkDashboardMetric(
            label="Stale",
            value=len(stale),
            tone="warning" if stale else "neutral",
            detail=f"{len(stale)} open assignment{' is' if len(stale) == 1 else 's are'} older than 30 minutes.",
        ),
        WorkDashboardMetric(
            label="Blocked",
            value=len(blocked),
            tone="danger" if blocked else "neutral",
            detail=f"{len(blocked)} assignment{' has' if len(blocked) == 1 else 's have'} readiness blockers.",
        ),
        WorkDashboardMetric(
            label="Approvals",
            value=pending_approvals,
            tone="warning" if pending_approvals else "neutral",
            detail=f"{pending_approvals} patch approval{' is' if pending_approvals == 1 else 's are'} waiting.",
        ),
    ]


def _dashboard_readiness(
    snapshot: WorkDashboardSnapshot,
    queue_health: list[WorkDashboardMetric],
) -> WorkDashboardReadiness:
    health = {item.label.lower(): item.value for item in queue_health}
    blockers: list[str] = []
    warnings: list[str] = []
    if not snapshot.agents:
        blockers.append("No AI teammate is present in the room.")
    if health.get("blocked", 0):
        blockers.append(f"{health['blocked']} assignment has readiness blockers." if health["blocked"] == 1 else f"{health['blocked']} assignments have readiness blockers.")
    if health.get("approvals", 0):
        blockers.append(f"{health['approvals']} patch approval is waiting." if health["approvals"] == 1 else f"{health['approvals']} patch approvals are waiting.")
    if health.get("stale", 0):
        warnings.append(f"{health['stale']} assignment is stale." if health["stale"] == 1 else f"{health['stale']} assignments are stale.")
    if health.get("open", 0):
        warnings.append(f"{health['open']} assignment is still open." if health["open"] == 1 else f"{health['open']} assignments are still open.")

    ready = not blockers and not warnings
    summary = (
        "Ready for handoff. No open assignments, stale work, blockers, or approvals."
        if ready
        else f"Needs attention: {len(blockers)} blocker{'s' if len(blockers) != 1 else ''}, {len(warnings)} warning{'s' if len(warnings) != 1 else ''}."
    )
    return WorkDashboardReadiness(
        ready=ready,
        state="ready" if ready else "needs_attention",
        summary=summary,
        blockers=blockers,
        warnings=warnings,
    )


def _status_value(status: Any) -> str:
    return getattr(status, "value", str(status))


def _assignment_age_minutes(assignment: Any) -> int:
    timestamp = assignment.updated_at or assignment.created_at
    return max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds() // 60))


@router.get("/rooms/{room_id}/memory", response_model=list[MemoryItem])
async def room_memory(
    room_id: str,
    q: str | None = None,
    kind: MemoryKind | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> list[MemoryItem]:
    _ensure_room_access(room_id, _user, service)
    return service.list_memory(room_id, q=q, kind=kind, status=status, limit=limit)


@router.get("/rooms/{room_id}/memory/health", response_model=MemoryHealthResponse)
async def room_memory_health(
    room_id: str,
    stale_days: int = Query(default=7, ge=1, le=365),
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> MemoryHealthResponse:
    _ensure_room_access(room_id, _user, service)
    return service.memory_health(room_id, stale_days=stale_days)


@router.get("/rooms/{room_id}/audit", response_model=list[AuditEvent])
async def room_audit(
    room_id: str,
    kind: str | None = Query(default=None, pattern="^(action|speaker|gate)$"),
    limit: int = Query(default=50, ge=1, le=200),
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> list[AuditEvent]:
    _ensure_room_access(room_id, _user, service)
    return service.list_audit_events(room_id, kind=kind, limit=limit)


@router.post("/rooms/{room_id}/audit/query", response_model=AuditQueryResponse)
async def query_room_audit(
    room_id: str,
    body: AuditQueryRequest,
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> AuditQueryResponse:
    _ensure_room_access(room_id, _user, service)
    return service.query_audit(room_id, body.question, limit=body.limit)


@router.get("/rooms/{room_id}/trace", response_model=RoomTraceResponse)
async def room_trace(
    room_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> RoomTraceResponse:
    _ensure_room_access(room_id, _user, service)
    return service.build_room_trace(room_id, limit=limit)


@router.post("/rooms/{room_id}/memory/query", response_model=MemoryQueryResponse)
async def query_room_memory(
    room_id: str,
    body: MemoryQueryRequest,
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    service: CollaborationService = Depends(get_collaboration_service),
) -> MemoryQueryResponse:
    _ensure_room_access(room_id, _user, service)
    return service.query_memory(room_id, body.question, limit=body.limit)


@router.post("/rooms/{room_id}/rag/query", response_model=RagQueryResponse)
async def query_room_rag(
    room_id: str,
    body: RagQueryRequest,
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> RagQueryResponse:
    _ensure_room_access(room_id, _user, service)
    try:
        return service.query_rag(
            room_id,
            body.question,
            settings,
            limit=body.limit,
            include_code=body.include_code,
        )
    except EmbeddingProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/provenance/query", response_model=ProvenanceQueryResponse)
async def query_room_provenance(
    room_id: str,
    body: ProvenanceQueryRequest,
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> ProvenanceQueryResponse:
    _ensure_room_access(room_id, _user, service)
    try:
        return service.query_provenance(
            room_id,
            body.question,
            settings,
            limit=body.limit,
            include_code=body.include_code,
            include_ontology=body.include_ontology,
        )
    except EmbeddingProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/rag/index", response_model=RagIndexResponse)
async def rebuild_room_rag_index(
    room_id: str,
    include_code: bool = Query(default=True),
    _user: UserPublic = Depends(require_permission("voice:use")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> RagIndexResponse:
    _ensure_room_access(room_id, _user, service)
    try:
        return service.rebuild_rag_index(room_id, settings, include_code=include_code)
    except EmbeddingProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/commands/route", response_model=CommandRouteResponse)
async def route_room_command(
    room_id: str,
    body: CommandRouteRequest,
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> CommandRouteResponse:
    _ensure_room_access(room_id, _user, service)
    return MeetingCommandRouter(collab=service, settings=service.settings_for_room(room_id, settings)).trace_prompt(
        room_id,
        body.text,
        memory_question_mode=body.memory_question_mode,
    )


@router.post("/rooms/{room_id}/code/query", response_model=CodeQueryResponse)
async def query_room_code(
    room_id: str,
    body: CodeQueryRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> CodeQueryResponse:
    _ensure_room_access(room_id, user, service)
    try:
        result = service.query_code(room_id, user, body.question, settings, limit=body.limit)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await events.publish(room_id, "code_query_answered", actor_id=user.id)
    return result


@router.get("/rooms/{room_id}/workspace/tree", response_model=WorkspaceTreeResponse)
async def room_workspace_tree(
    room_id: str,
    limit: int = Query(default=120, ge=1, le=500),
    user: UserPublic = Depends(require_permission("voice:use")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> WorkspaceTreeResponse:
    _ensure_room_access(room_id, user, service)
    try:
        return WorkspaceCodeService(service.settings_for_room(room_id, settings)).tree(limit=limit)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/workspace/git/status", response_model=GitStatusResponse)
async def room_workspace_git_status(
    room_id: str,
    user: UserPublic = Depends(require_permission("voice:use")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> GitStatusResponse:
    _ensure_room_access(room_id, user, service)
    try:
        return WorkspaceGitService(service.settings_for_room(room_id, settings)).status()
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/workspace/git/diff", response_model=GitDiffResponse)
async def room_workspace_git_diff(
    room_id: str,
    user: UserPublic = Depends(require_permission("voice:use")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
) -> GitDiffResponse:
    _ensure_room_access(room_id, user, service)
    try:
        return WorkspaceGitService(service.settings_for_room(room_id, settings)).diff()
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/messages", response_model=TimelineMessage)
async def add_message(
    room_id: str,
    body: TextMessageRequest,
    user: UserPublic = Depends(require_permission("voice:use")),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> TimelineMessage:
    _ensure_room_access(room_id, user, service)
    message = service.add_user_message(room_id, user, body)
    await events.publish(room_id, "message_created", actor_id=user.id)
    return message


@router.post("/rooms/{room_id}/actions/{action_id}/approve", response_model=AgentAction)
async def approve_action(
    room_id: str,
    action_id: str,
    body: ActionDecisionRequest | None = None,
    user: UserPublic = Depends(require_permission("agent:approve")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> AgentAction:
    _ensure_room_access(room_id, user, service)
    try:
        action = service.approve_action(
            room_id,
            action_id,
            user,
            settings,
            note=body.note if body else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await events.publish(room_id, "action_updated", actor_id=user.id, payload={"action_id": action_id})
    return action


@router.post("/rooms/{room_id}/actions/{action_id}/reject", response_model=AgentAction)
async def reject_action(
    room_id: str,
    action_id: str,
    body: ActionDecisionRequest | None = None,
    user: UserPublic = Depends(require_permission("agent:approve")),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> AgentAction:
    _ensure_room_access(room_id, user, service)
    try:
        action = service.reject_action(room_id, action_id, user, note=body.note if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await events.publish(room_id, "action_updated", actor_id=user.id, payload={"action_id": action_id})
    return action


@router.post("/rooms/{room_id}/actions/{action_id}/commit", response_model=AgentAction)
async def commit_action(
    room_id: str,
    action_id: str,
    body: ActionCommitRequest | None = None,
    user: UserPublic = Depends(require_permission("agent:approve")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> AgentAction:
    _ensure_room_access(room_id, user, service)
    try:
        action = service.commit_action(
            room_id,
            action_id,
            user,
            settings,
            message=body.message if body else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await events.publish(room_id, "action_updated", actor_id=user.id, payload={"action_id": action_id})
    return action


@router.post("/rooms/{room_id}/actions/{action_id}/pull-request", response_model=ActionPullRequestResponse)
async def create_action_pull_request(
    room_id: str,
    action_id: str,
    body: ActionPullRequestRequest | None = None,
    user: UserPublic = Depends(require_permission("agent:approve")),
    settings: Settings = Depends(get_settings),
    service: CollaborationService = Depends(get_collaboration_service),
    events: RoomEventHub = Depends(get_room_event_hub),
) -> ActionPullRequestResponse:
    _ensure_room_access(room_id, user, service)
    request = body or ActionPullRequestRequest()
    try:
        response = service.create_pull_request_plan(
            room_id,
            action_id,
            user,
            settings,
            dry_run=request.dry_run,
            title=request.title,
            body=request.body,
            base_branch=request.base_branch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not request.dry_run:
        await events.publish(room_id, "action_updated", actor_id=user.id, payload={"action_id": action_id})
    return response


@router.websocket("/rooms/{room_id}/events")
async def room_events(
    websocket: WebSocket,
    room_id: str,
) -> None:
    try:
        user = _websocket_user(websocket)
    except Exception:
        await websocket.close(code=1008, reason="Not authenticated")
        return

    events = _websocket_dependency(websocket, get_room_event_hub)
    service = _websocket_dependency(websocket, get_collaboration_service)
    try:
        _ensure_room_access(room_id, user, service)
    except HTTPException:
        await websocket.close(code=1008, reason="Join this room before subscribing")
        return
    await websocket.accept()
    queue = await events.subscribe(room_id)
    await websocket.send_json(
        {
            "type": "room_event",
            "event": "connected",
            "room_id": room_id,
            "actor_id": user.id,
            "payload": {},
        }
    )
    try:
        while True:
            event_task = asyncio.create_task(queue.get())
            disconnect_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait(
                {event_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if disconnect_task in done:
                disconnect_task.result()
                continue

            await websocket.send_json(event_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    finally:
        await events.unsubscribe(room_id, queue)


@router.get("/rooms/{room_id}/handoff", response_model=HandoffSummary)
async def handoff(
    room_id: str,
    user: UserPublic = Depends(get_current_user),
    service: CollaborationService = Depends(get_collaboration_service),
) -> HandoffSummary:
    _ensure_room_access(room_id, user, service)
    return service.build_handoff(room_id, user)


def _room_clone_target(room_id: str, target_path: str | None, remote_url: str, settings: Settings) -> Path:
    if target_path and target_path.strip():
        target = Path(target_path.strip()).expanduser()
        if not target.is_absolute():
            raise HTTPException(status_code=400, detail="Target path must be absolute.")
        return _ensure_clone_target_inside_root(target, settings)
    repo_name = _github_repo_name(remote_url)
    if not repo_name:
        raise HTTPException(status_code=400, detail="Could not derive repository name from GitHub URL.")
    safe_room = "".join(char if char.isalnum() or char in "-_." else "-" for char in room_id).strip(".") or "room"
    return _ensure_clone_target_inside_root(settings.workspace_clone_root / safe_room / repo_name, settings)


def _websocket_user(websocket: WebSocket) -> UserPublic:
    token = websocket.query_params.get("token")
    if not token:
        raise ValueError("Missing WebSocket token")
    from app.config import get_settings

    settings = _websocket_dependency(websocket, get_settings)
    user_store = _websocket_dependency(websocket, get_user_store)
    payload = decode_access_token(token, settings)
    user = user_store.get_by_id(payload.sub)
    if user is None:
        raise ValueError("inactive user")
    return to_public_user(user)


def _websocket_dependency(websocket: WebSocket, dependency):
    provider = websocket.app.dependency_overrides.get(dependency, dependency)
    return provider()
