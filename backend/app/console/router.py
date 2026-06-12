from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.auth.models import UserPublic
from app.config import Settings, get_settings
from app.console.service import ConsoleBootstrap, build_integrations, get_workspace_info

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/bootstrap", response_model=ConsoleBootstrap)
async def bootstrap(
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    workspace = get_workspace_info(settings.voiceops_workspace)
    return ConsoleBootstrap(
        user=user.model_dump(),
        workspace=workspace,
        incidents=[],
        metrics=[],
        artifacts=[],
        integrations=build_integrations(workspace),
        status_counts={"critical": 0, "active": 0},
    )
