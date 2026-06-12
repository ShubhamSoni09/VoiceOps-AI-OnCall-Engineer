from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.auth.models import UserPublic
from app.config import Settings, get_settings
from app.console.service import (
    ConsoleBootstrap,
    _app_source,
    build_integrations,
    get_workspace_info,
    run_pytest_status,
)
from app.console.mock_data import (
    build_sandbox_mock_artifacts,
    build_sandbox_mock_incidents,
    build_sandbox_mock_metrics,
)

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/bootstrap", response_model=ConsoleBootstrap)
async def bootstrap(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    workspace = get_workspace_info(settings.voiceops_workspace)

    if not workspace.connected:
        return ConsoleBootstrap(
            user=_user.model_dump(),
            workspace=workspace,
            incidents=[],
            metrics=[],
            artifacts=[],
            integrations=build_integrations(workspace),
            status_counts={"critical": 0, "active": 0},
        )

    all_pass, failing = run_pytest_status(workspace)
    source = _app_source(workspace)
    incidents, status_counts = build_sandbox_mock_incidents(
        source,
        all_tests_pass=all_pass,
        failing_tests=failing,
    )
    return ConsoleBootstrap(
        user=_user.model_dump(),
        workspace=workspace,
        incidents=incidents,
        metrics=build_sandbox_mock_metrics(all_tests_pass=all_pass, failing_tests=failing),
        artifacts=build_sandbox_mock_artifacts(all_tests_pass=all_pass, failing_tests=failing),
        integrations=build_integrations(workspace),
        status_counts=status_counts,
    )
