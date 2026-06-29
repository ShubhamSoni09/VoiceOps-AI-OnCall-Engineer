from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.long_memory.models import LongMemoryArchiveResponse, LongMemoryQueryResponse, LongMemoryRecord
from app.long_memory.service import LongMemoryService
from app.long_memory.store import LongMemoryStore

router = APIRouter(prefix="/memory", tags=["long-memory"])


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before accessing long memory.")


def get_request_long_memory_service(
    settings: Settings = Depends(get_settings),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> LongMemoryService:
    return LongMemoryService(LongMemoryStore(settings.long_memory_path), collab)


@router.post("/rooms/{room_id}/archive", response_model=LongMemoryArchiveResponse)
async def archive_room_long_memory(
    room_id: str,
    user: UserPublic = Depends(require_permission("voice:use")),
    collab: CollaborationService = Depends(get_collaboration_service),
    service: LongMemoryService = Depends(get_request_long_memory_service),
) -> LongMemoryArchiveResponse:
    _ensure_room_access(room_id, user, collab)
    return service.archive_room(room_id)


@router.get("/rooms/{room_id}/long", response_model=list[LongMemoryRecord])
async def list_room_long_memory(
    room_id: str,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user: UserPublic = Depends(get_current_user),
    collab: CollaborationService = Depends(get_collaboration_service),
    service: LongMemoryService = Depends(get_request_long_memory_service),
) -> list[LongMemoryRecord]:
    _ensure_room_access(room_id, user, collab)
    return service.list_records(room_id, kind=kind, limit=limit)


@router.get("/rooms/{room_id}/long/query", response_model=LongMemoryQueryResponse)
async def query_room_long_memory(
    room_id: str,
    q: str = Query(min_length=1, max_length=500),
    kind: str | None = None,
    limit: int = Query(default=8, ge=1, le=50),
    user: UserPublic = Depends(get_current_user),
    collab: CollaborationService = Depends(get_collaboration_service),
    service: LongMemoryService = Depends(get_request_long_memory_service),
) -> LongMemoryQueryResponse:
    _ensure_room_access(room_id, user, collab)
    return service.query(room_id, q, kind=kind, limit=limit)
