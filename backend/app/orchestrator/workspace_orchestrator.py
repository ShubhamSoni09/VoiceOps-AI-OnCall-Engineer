from __future__ import annotations

import asyncio
import difflib
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.orchestrator.test_summary import (
    build_investigation_summary,
    build_patch_summary,
    build_test_summary,
)
from app.integrations.github.api import GitHubApiError, create_fix_pull_request, parse_github_repo
from app.integrations.github.store import get_github_token_store
from app.integrations.github.user_repo import get_user_selected_repo
from app.orchestrator.workspace_patches import apply_best_patch, apply_targeted_patch, detect_patch_target
from app.voice_agent.models import IncidentAction, NormalizedCommand, OrchestratorResult, VoiceIntent
from app.workspace.tools import (
    WorkspaceError,
    arun_command_streaming,
    list_directory,
    read_file,
    run_command,
    write_file,
)
from app.workspace.ops import resolve_ops_workspace

_CASUAL_CHAT_RE = re.compile(
    r"\b(how are you|how'?s it going|hello|hi|hey|thanks|thank you|what are you doing|what you doing)\b",
    re.I,
)
_WORKSPACE_QUESTION_RE = re.compile(
    r"\b(issue|incident|problem|bug|error|fail|wrong|broken|code|test|sandbox|service|endpoint|"
    r"checkout|api|root cause|diagnose|investigate|explain|describe|tell me|what is|what's)\b",
    re.I,
)

WORKSPACE_ACTIONS = {
    IncidentAction.INVESTIGATE,
    IncidentAction.DIAGNOSE,
    IncidentAction.TEST,
    IncidentAction.STATUS,
    IncidentAction.PATCH,
    IncidentAction.CREATE_PR,
    IncidentAction.UNKNOWN,
}


class WorkspaceOrchestrator:
    """Run workspace tools for voice commands when a repo is connected."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def connected(self) -> bool:
        from app.console.service import resolve_configured_workspace

        return resolve_configured_workspace(self._settings.voiceops_workspace) is not None

    def _workspace_for_user(self, _user_id: str | None) -> str | None:
        return self._settings.voiceops_workspace

    async def execute(
        self,
        command: NormalizedCommand,
        transcript: str,
        progress: asyncio.Queue | None = None,
        user_id: str | None = None,
    ) -> OrchestratorResult | None:
        workspace = self._workspace_for_user(user_id)
        if workspace is None:
            return None

        ops_workspace = str(resolve_ops_workspace(workspace) or workspace)

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
        }:
            return OrchestratorResult(
                executed=False,
                action=action.value,
                summary="I have a plan ready — approve it in the console before I deploy or roll back.",
                pending_approval=True,
            )

        try:
            if action in {IncidentAction.INVESTIGATE, IncidentAction.DIAGNOSE, IncidentAction.STATUS}:
                return await self._investigate(ops_workspace, action, progress)
            if action == IncidentAction.TEST:
                return await self._run_tests(ops_workspace, progress)
            if action == IncidentAction.PATCH:
                return await self._patch(ops_workspace, transcript, progress, user_id=user_id)
            if action == IncidentAction.CREATE_PR:
                return await self._create_pr(ops_workspace, transcript, user_id, progress)
            if action == IncidentAction.UNKNOWN:
                if command.intent in {VoiceIntent.GENERAL_QUERY, VoiceIntent.UNKNOWN}:
                    if _is_workspace_question(transcript):
                        return await self._general_workspace_query(
                            ops_workspace, transcript, progress, user_id=user_id
                        )
                    return None
                return await self._general_workspace_query(
                    ops_workspace, transcript, progress, user_id=user_id
                )
        except WorkspaceError as exc:
            return OrchestratorResult(
                executed=False,
                action=action.value,
                summary=f"Workspace error: {exc}",
            )
        except GitHubApiError as exc:
            return OrchestratorResult(
                executed=False,
                action=action.value,
                summary=f"GitHub error: {exc}",
            )

        return None

    async def _pytest_streaming(
        self,
        workspace: str | None,
        progress: asyncio.Queue | None,
    ) -> dict:
        """Run pytest, optionally streaming output lines to the progress queue."""
        if progress is None:
            return run_command("python -m pytest -q", configured=workspace)

        lines: list[str] = []
        exit_code = 1
        async for event in arun_command_streaming("python -m pytest -q", configured=workspace):
            if event["type"] == "line":
                lines.append(event["text"])
                await progress.put({"type": "output", "chunk": event["text"]})
            else:
                exit_code = event["code"]

        output = "".join(lines)
        return {"command": "python -m pytest -q", "exit_code": exit_code, "stdout": output, "stderr": ""}

    async def _investigate(
        self,
        workspace: str | None,
        action: IncidentAction,
        progress: asyncio.Queue | None = None,
    ) -> OrchestratorResult:
        root_name = Path(workspace).name if workspace else "workspace"

        await _emit(progress, "step", label="Scanning workspace files…")
        _prepare_workspace(workspace)
        entries = list_directory(".", configured=workspace)
        file_names = [e["path"] for e in entries if e["type"] == "file"][:8]

        app_source = ""
        try:
            app_source = read_file("app.py", configured=workspace)
        except WorkspaceError:
            pass

        await _emit(progress, "step", label="Running pytest…")
        test_result = await self._pytest_streaming(workspace, progress)

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
            all_tests_pass=passed,
        )

    async def _run_tests(
        self,
        workspace: str | None,
        progress: asyncio.Queue | None = None,
    ) -> OrchestratorResult:
        _prepare_workspace(workspace)
        root_name = Path(workspace).name if workspace else "workspace"

        app_source = ""
        try:
            app_source = read_file("app.py", configured=workspace)
        except WorkspaceError:
            pass

        await _emit(progress, "step", label="Running pytest…")
        test_result = await self._pytest_streaming(workspace, progress)
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
            all_tests_pass=passed,
        )

    async def _patch(
        self,
        workspace: str | None,
        transcript: str,
        progress: asyncio.Queue | None = None,
        *,
        user_id: str | None = None,
    ) -> OrchestratorResult:
        _prepare_workspace(workspace)

        await _emit(progress, "step", label="Running tests to find failures…")
        test_before = await self._pytest_streaming(workspace, progress)
        if test_before["exit_code"] == 0:
            return OrchestratorResult(
                executed=True,
                action=IncidentAction.PATCH.value,
                summary="Tests already pass — nothing to patch right now.",
                command_output=_truncate(test_before["stdout"], 400),
                all_tests_pass=True,
            )

        await _emit(progress, "step", label="Reading app.py…")
        try:
            original = read_file("app.py", configured=workspace)
        except WorkspaceError:
            return OrchestratorResult(
                executed=False,
                action=IncidentAction.PATCH.value,
                summary="Could not find app.py in the workspace to patch.",
            )

        failure = test_before["stdout"] + test_before["stderr"]
        target = detect_patch_target(transcript, failure)

        await _emit(progress, "step", label="Generating fix…")
        updated = await self._generate_fix(original, failure, transcript, target)
        change_desc = "Updated app.py."

        if not updated or updated.strip() == original.strip():
            updated, change_desc = apply_best_patch(original, transcript, failure)
        elif target:
            _, change_desc = apply_targeted_patch(original, target)

        if updated.strip() == original.strip():
            return OrchestratorResult(
                executed=True,
                action=IncidentAction.PATCH.value,
                summary=change_desc,
                command_output=_truncate(failure, 400),
            )

        patch_diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile="app.py (before)",
                tofile="app.py (after)",
                lineterm="",
            )
        )

        await _emit(progress, "step", label="Applying patch to app.py…")
        await _emit(progress, "diff", text=patch_diff)
        write_file("app.py", updated, configured=workspace)

        await _emit(progress, "step", label="Verifying fix with pytest…")
        test_after = await self._pytest_streaming(workspace, progress)
        passed = test_after["exit_code"] == 0
        output = _truncate(test_after["stdout"] + test_after["stderr"], 600)

        if passed:
            summary = build_patch_summary(True, change=change_desc)
        else:
            summary = build_patch_summary(False, change=change_desc)

        pr_url = None
        artifacts = [
            {
                "type": "pr",
                "title": "app.py patched in sandbox",
                "subtitle": "pytest passed" if passed else "pytest still failing",
            }
        ]
        if passed and self._settings.github_auto_pr:
            pr_url, pr_artifact = await self._open_github_pr(
                workspace,
                user_id,
                {"app.py": updated},
                title=f"VoiceOps: {change_desc}",
                body=f"Automated fix from VoiceOps.\n\nRequest: {transcript}",
                progress=progress,
            )
            if pr_artifact:
                artifacts = [pr_artifact, *artifacts]
                summary = f"{summary} Opened pull request on GitHub."

        return OrchestratorResult(
            executed=True,
            action=IncidentAction.PATCH.value,
            summary=summary,
            files_changed=["app.py"],
            diff=patch_diff or None,
            artifacts=artifacts,
            command_output=output,
            all_tests_pass=passed,
            pr_url=pr_url,
        )

    async def _create_pr(
        self,
        workspace: str | None,
        transcript: str,
        user_id: str | None,
        progress: asyncio.Queue | None = None,
    ) -> OrchestratorResult:
        await _emit(progress, "step", label="Preparing pull request…")
        files: dict[str, str] = {}
        for path in ("app.py",):
            try:
                files[path] = read_file(path, configured=workspace)
            except WorkspaceError:
                continue
        if not files:
            return OrchestratorResult(
                executed=False,
                action=IncidentAction.CREATE_PR.value,
                summary="No workspace files found to include in a pull request.",
            )

        pr_url, pr_artifact = await self._open_github_pr(
            workspace,
            user_id,
            files,
            title="VoiceOps: on-call fix",
            body=f"Pull request requested via VoiceOps.\n\nRequest: {transcript}",
            progress=progress,
        )
        if not pr_url:
            return OrchestratorResult(
                executed=False,
                action=IncidentAction.CREATE_PR.value,
                summary="Connect GitHub and select a repository before opening a PR.",
            )

        return OrchestratorResult(
            executed=True,
            action=IncidentAction.CREATE_PR.value,
            summary="Opened a pull request on GitHub. Review and merge when ready.",
            files_changed=list(files.keys()),
            artifacts=[pr_artifact] if pr_artifact else [],
            pr_url=pr_url,
        )

    async def _open_github_pr(
        self,
        workspace: str | None,
        user_id: str | None,
        files: dict[str, str],
        *,
        title: str,
        body: str,
        progress: asyncio.Queue | None = None,
    ) -> tuple[str | None, dict | None]:
        if not user_id:
            return None, None
        repo_slug = get_user_selected_repo(user_id, self._settings)
        if not repo_slug:
            return None, None
        conn = get_github_token_store().get(user_id)
        if conn is None:
            return None, None

        owner, repo = parse_github_repo(repo_slug)
        await _emit(progress, "step", label="Opening GitHub pull request…")
        pr = await create_fix_pull_request(
            conn.access_token,
            owner,
            repo,
            files=files,
            title=title,
            body=body,
        )
        url = str(pr.get("url") or "")
        artifact = {
            "type": "pr",
            "title": f"PR #{pr.get('number')} · {repo}",
            "subtitle": url,
            "url": url,
        }
        return url or None, artifact

    async def _general_workspace_query(
        self,
        workspace: str | None,
        transcript: str,
        progress: asyncio.Queue | None = None,
        *,
        user_id: str | None = None,
    ) -> OrchestratorResult:
        lower = transcript.lower()
        if any(word in lower for word in ("pull request", "open pr", "create pr")):
            return await self._create_pr(workspace, transcript, user_id, progress)
        if any(word in lower for word in ("fix", "patch", "health", "broken", "failing")):
            return await self._patch(workspace, transcript, progress, user_id=user_id)
        if any(word in lower for word in ("test", "pytest")):
            return await self._run_tests(workspace, progress)
        return await self._investigate(workspace, IncidentAction.INVESTIGATE, progress)

    async def _generate_fix(
        self,
        source: str,
        failure: str,
        transcript: str,
        target: str | None,
    ) -> str | None:
        if self._settings.llm_provider != "openai" or not self._settings.openai_api_key:
            return None

        from openai import AsyncOpenAI

        focus = {
            "health": "Add GET /health returning {\"status\": \"ok\"}.",
            "charge": "Add GET /v2/charge that accepts amount query param and returns {\"status\": \"charged\", ...}.",
            "metrics": "Fix GET /metrics to return error_rate (not error_rate_pct) with value under 1.0.",
        }.get(target or "", "Fix only what the failing pytest tests require.")

        client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        response = await client.chat.completions.create(
            model=self._settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You fix Python FastAPI code in a sandbox repo. "
                        f"{focus} "
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


async def _emit(progress: asyncio.Queue | None, event_type: str, **kwargs: Any) -> None:
    if progress is not None:
        await progress.put({"type": event_type, **kwargs})


def _is_workspace_question(transcript: str) -> bool:
    if _CASUAL_CHAT_RE.search(transcript) and not _WORKSPACE_QUESTION_RE.search(transcript):
        return False
    return bool(_WORKSPACE_QUESTION_RE.search(transcript))


def _prepare_workspace(workspace: str | None) -> None:
    root = Path(workspace) if workspace else None
    if root is None or not root.is_dir():
        return
    requirements = root / "requirements.txt"
    if requirements.is_file():
        run_command("python -m pip install -q -r requirements.txt", configured=workspace)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
