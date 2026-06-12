from __future__ import annotations

from app.config import Settings
from app.console.service import WorkspaceInfo, get_workspace_info


def get_workspace_info_for_user(_user_id: str, settings: Settings) -> WorkspaceInfo:
    """Return workspace info for the configured folder (VOICEOPS_WORKSPACE)."""
    return get_workspace_info(settings.voiceops_workspace)
