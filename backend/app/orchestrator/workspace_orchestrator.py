from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.orchestrator.test_summary import (
    build_investigation_summary,
    build_patch_summary,
    build_test_summary,
)
from app.voice_agent.models import IncidentAction, NormalizedCommand, OrchestratorResult, VoiceIntent
from app.workspace.tools import (
    WorkspaceError,
    list_directory,
    read_file,
    resolve_configured_workspace,
    run_command,
    write_file,
)

WORKSPACE_ACTIONS = {
    IncidentAction.INVESTIGATE,
    IncidentAction.DIAGNOSE,
    IncidentAction.TEST,
    IncidentAction.STATUS,
    IncidentAction.PATCH,
    IncidentAction.UNKNOWN,
}


class WorkspaceOrchestrator:
    """Run workspace tools for voice commands when a repo is connected."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace_path = resolve_configured_workspace(settings.voiceops_workspace)

    @property
    def connected(self) -> bool:
        return self._workspace_path is not None

    async def execute(
        self,
        command: NormalizedCommand,
        transcript: str,
    ) -> OrchestratorResult | None:
        if not self.connected:
            return None

        action = command.action
        if action not in WORKSPACE_ACTIONS:
            return OrchestratorResult(
                executed=False,
                action=action.value,
                summary="That action is not wired to the workspace yet.",
            )

        if command.requires_approval and action in {
            IncidentAction.DEPLOY,
            IncidentAction.ROLLBACK,
            IncidentAction.CREATE_PR,
        }:
            return OrchestratorResult(
                executed=False,
                action=action.value,
                summary="I have a plan ready — approve it in the console before I deploy or open a PR.",
                pending_approval=True,
            )

        workspace = self._settings.voiceops_workspace

        try:
            if action in {IncidentAction.INVESTIGATE, IncidentAction.DIAGNOSE, IncidentAction.STATUS}:
                return await self._investigate(workspace, action)
            if action == IncidentAction.TEST:
                return await self._run_tests(workspace)
            if action == IncidentAction.PATCH:
                return await self._patch(workspace, transcript)
            if action == IncidentAction.UNKNOWN:
                if command.intent in {VoiceIntent.GENERAL_QUERY, VoiceIntent.UNKNOWN}:
                    return None
                return await self._general_workspace_query(workspace, transcript)
        except WorkspaceError as exc:
            return OrchestratorResult(
                executed=False,
                action=action.value,
                summary=f"Workspace error: {exc}",
            )

        return None

    async def _investigate(self, workspace: str | None, action: IncidentAction) -> OrchestratorResult:
        root_name = self._workspace_path.name if self._workspace_path else "workspace"
        _prepare_workspace(workspace)
        entries = list_directory(".", configured=workspace)
        file_names = [e["path"] for e in entries if e["type"] == "file"][:8]

        app_source = ""
        try:
            app_source = read_file("app.py", configured=workspace)
        except WorkspaceError:
            pass

        test_result = run_command("python -m pytest -q", configured=workspace)

        summary = build_investigation_summary(
            root_name,
            file_names,
            app_source or None,
            test_result,
        )
        passed = test_result["exit_code"] == 0

        artifacts = [
            {
                "type": "logs",
                "title": f"pytest · {root_name}",
                "subtitle": "passed" if passed else f"{test_result['exit_code']} failed",
            }
        ]

        return OrchestratorResult(
            executed=True,
            action=action.value,
            summary=summary,
            artifacts=artifacts,
            command_output=_truncate(test_result["stdout"] + test_result["stderr"], 800),
            files_changed=[],
        )

    async def _run_tests(self, workspace: str | None) -> OrchestratorResult:
        _prepare_workspace(workspace)
        root_name = self._workspace_path.name if self._workspace_path else "workspace"
        app_source = ""
        try:
            app_source = read_file("app.py", configured=workspace)
        except WorkspaceError:
            pass

        test_result = run_command("python -m pytest -q", configured=workspace)
        passed = test_result["exit_code"] == 0
        output = _truncate(test_result["stdout"] + test_result["stderr"], 800)
        summary = build_test_summary(root_name, test_result, app_source=app_source or None)

        return OrchestratorResult(
            executed=True,
            action=IncidentAction.TEST.value,
            summary=summary,
            artifacts=[
                {
                    "type": "logs",
                    "title": "pytest results",
                    "subtitle": "passed" if passed else "failed",
                }
            ],
            command_output=output,
        )

    async def _patch(self, workspace: str | None, transcript: str) -> OrchestratorResult:
        _prepare_workspace(workspace)
        test_before = run_command("python -m pytest -q", configured=workspace)
        if test_before["exit_code"] == 0:
            return OrchestratorResult(
                executed=True,
                action=IncidentAction.PATCH.value,
                summary="Tests already pass — nothing to patch right now.",
                command_output=_truncate(test_before["stdout"], 400),
            )

        try:
            original = read_file("app.py", configured=workspace)
        except WorkspaceError:
            return OrchestratorResult(
                executed=False,
                action=IncidentAction.PATCH.value,
                summary="Could not find app.py in the workspace to patch.",
            )

        failure = test_before["stdout"] + test_before["stderr"]
        updated = await self._generate_fix(original, failure, transcript)
        if not updated or updated.strip() == original.strip():
            updated = _heuristic_health_fix(original)

        write_file("app.py", updated, configured=workspace)
        test_after = run_command("python -m pytest -q", configured=workspace)
        passed = test_after["exit_code"] == 0
        output = _truncate(test_after["stdout"] + test_after["stderr"], 600)

        if passed:
            summary = build_patch_summary(True)
        else:
            summary = build_patch_summary(False)

        return OrchestratorResult(
            executed=True,
            action=IncidentAction.PATCH.value,
            summary=summary,
            files_changed=["app.py"],
            artifacts=[
                {
                    "type": "pr",
                    "title": "app.py patched in sandbox",
                    "subtitle": "pytest passed" if passed else "pytest still failing",
                }
            ],
            command_output=output,
        )

    async def _general_workspace_query(self, workspace: str | None, transcript: str) -> OrchestratorResult:
        lower = transcript.lower()
        if any(word in lower for word in ("fix", "patch", "health", "broken", "failing")):
            return await self._patch(workspace, transcript)
        if any(word in lower for word in ("test", "pytest")):
            return await self._run_tests(workspace)
        return await self._investigate(workspace, IncidentAction.INVESTIGATE)

    async def _generate_fix(self, source: str, failure: str, transcript: str) -> str | None:
        if self._settings.llm_provider != "openai" or not self._settings.openai_api_key:
            return None

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        response = await client.chat.completions.create(
            model=self._settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You fix Python FastAPI code in a sandbox repo. "
                        "Return ONLY the complete updated app.py file contents. No markdown fences."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request: {transcript}\n\n"
                        f"Test failure:\n{failure}\n\n"
                        f"Current app.py:\n{source}"
                    ),
                },
            ],
            temperature=0.1,
        )
        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```(?:python)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        return content.strip() or None


def _heuristic_health_fix(source: str) -> str:
    if '@app.get("/health")' in source or "def health(" in source:
        return source
    insert = (
        '\n\n@app.get("/health")\n'
        "def health() -> dict[str, str]:\n"
        '    return {"status": "ok"}\n'
    )
    if "# Intentionally missing" in source or "# GET /health is intentionally missing" in source:
        if "# GET /health is intentionally missing" in source:
            return source.split("# GET /health is intentionally missing")[0].rstrip() + insert
        return source.split("# Intentionally missing")[0].rstrip() + insert
    return source.rstrip() + insert


def _prepare_workspace(workspace: str | None) -> None:
    root = resolve_configured_workspace(workspace) if workspace else None
    if root is None:
        return
    requirements = root / "requirements.txt"
    if requirements.is_file():
        run_command("python -m pip install -q -r requirements.txt", configured=workspace)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
