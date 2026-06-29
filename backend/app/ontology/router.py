from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_current_user
from app.auth.models import UserPublic
from app.collab.service import CollaborationService, get_collaboration_service
from app.ontology.models import OntologyGraph, OntologyQueryResponse
from app.ontology.service import OntologyService

router = APIRouter(prefix="/ontology", tags=["ontology"])


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before accessing ontology data.")


def get_ontology_service(
    collab: CollaborationService = Depends(get_collaboration_service),
) -> OntologyService:
    return OntologyService(collab)


@router.get("/rooms/{room_id}", response_model=OntologyGraph)
async def room_ontology(
    room_id: str,
    user: UserPublic = Depends(get_current_user),
    collab: CollaborationService = Depends(get_collaboration_service),
    service: OntologyService = Depends(get_ontology_service),
) -> OntologyGraph:
    _ensure_room_access(room_id, user, collab)
    return service.build_graph(room_id)


@router.get("/rooms/{room_id}/query", response_model=OntologyQueryResponse)
async def query_room_ontology(
    room_id: str,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=12, ge=1, le=50),
    user: UserPublic = Depends(get_current_user),
    collab: CollaborationService = Depends(get_collaboration_service),
    service: OntologyService = Depends(get_ontology_service),
) -> OntologyQueryResponse:
    _ensure_room_access(room_id, user, collab)
    return service.query(room_id, q, limit=limit)
