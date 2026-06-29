from typing import Any

from app.agent_runtime import AgentRuntimeService
from app.config import Settings
from app.voice_agent.models import OrchestratorResult


class LocalTestAgentAdapter:
    """Validate proposed patch files in a disposable workspace before human approval."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_patch(self, result: OrchestratorResult) -> dict[str, Any]:
        payload = result.approval_payload or {}
        files = payload.get("files") or {}
        command = payload.get("test_command") or result.approval.get("test_command") or "python -m pytest -q"
        return AgentRuntimeService(self._settings).validate_proposed_patch(
            proposed_files=files,
            command=command,
        )
