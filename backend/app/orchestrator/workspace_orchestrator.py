from __future__ import annotations

import difflib
import re
from typing import Any

from app.config import Settings
from app.llm import LLMMessage, LLMRequest
from app.llm.service import get_llm_runtime
from app.orchestrator.test_summary import (
    build_investigation_summary,
    build_test_summary,
)
from app.voice_agent.models import IncidentAction, NormalizedCommand, OrchestratorResult, VoiceIntent
from app.workspace.git import WorkspaceGitService
from app.workspace.github import is_github_workspace
from app.workspace.service import WorkspaceCodeService
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
    IncidentAction.EXPLAIN_CODE,
    IncidentAction.FIND_BUG,
    IncidentAction.GIT_STATUS,
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
        self._github_workspace = is_github_workspace(settings.voiceops_workspace)

    @property
    def connected(self) -> bool:
        return self._workspace_path is not None or self._github_workspace

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
            if action == IncidentAction.EXPLAIN_CODE:
                return await self._explain_code(transcript)
            if action == IncidentAction.FIND_BUG:
                return await self._find_bug(workspace)
            if action == IncidentAction.GIT_STATUS:
                return await self._git_status()
            if action == IncidentAction.TEST:
                return await self._run_tests(workspace)
            if action == IncidentAction.PATCH:
                result = await self._patch(workspace, _contextual_transcript(transcript, command))
                resolved = command.parameters.get("resolved_context")
                if result.pending_approval and isinstance(resolved, dict):
                    result.approval["resolved_context"] = resolved
                return result
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
        if self._github_workspace:
            tree = WorkspaceCodeService(self._settings).tree(limit=8)
            file_names = [item.path for item in tree.files[:8]]
            summary = f"I inspected the GitHub repository. Key files: {', '.join(file_names) or 'none found'}. Local tests are skipped; use PR checks."
            return OrchestratorResult(
                executed=True,
                action=action.value,
                summary=summary,
                artifacts=[{"type": "logs", "title": "GitHub repository", "subtitle": f"{tree.total_files} files indexed"}],
                files_changed=[],
            )
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
        if self._github_workspace:
            return OrchestratorResult(
                executed=True,
                action=IncidentAction.TEST.value,
                summary="Local tests are not available in GitHub-direct mode. Open or update a PR and use GitHub checks.",
                artifacts=[{"type": "logs", "title": "GitHub checks", "subtitle": "local checkout skipped"}],
                command_output="Skipped local tests in GitHub-direct mode.",
            )
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

    async def _explain_code(self, transcript: str) -> OrchestratorResult:
        answer = WorkspaceCodeService(self._settings).query(transcript, limit=8)
        artifacts = [
            {
                "type": "book",
                "title": ref.path,
                "subtitle": f"line {ref.line}",
            }
            for ref in answer.references[:3]
        ]
        return OrchestratorResult(
            executed=True,
            action=IncidentAction.EXPLAIN_CODE.value,
            summary=answer.answer,
            artifacts=artifacts,
            files_changed=[],
        )

    async def _find_bug(self, workspace: str | None) -> OrchestratorResult:
        if self._github_workspace:
            return OrchestratorResult(
                executed=True,
                action=IncidentAction.FIND_BUG.value,
                summary="I can inspect GitHub code directly, but bug validation needs GitHub PR checks because there is no local checkout.",
                artifacts=[{"type": "logs", "title": "bug scan", "subtitle": "PR checks required"}],
                command_output="Skipped local bug scan in GitHub-direct mode.",
                files_changed=[],
            )
        _prepare_workspace(workspace)
        test_result = run_command("python -m pytest -q", configured=workspace)
        passed = test_result["exit_code"] == 0
        output = _truncate(test_result["stdout"] + test_result["stderr"], 800)
        summary = (
            "I ran the tests and did not find a failing bug signal yet. No patch was proposed."
            if passed
            else "I found failing tests. I am reporting the failure only; say fix or patch when you want a proposed change."
        )
        return OrchestratorResult(
            executed=True,
            action=IncidentAction.FIND_BUG.value,
            summary=summary,
            artifacts=[
                {
                    "type": "logs",
                    "title": "bug scan",
                    "subtitle": "no failing tests" if passed else "failing tests found",
                }
            ],
            command_output=output,
            files_changed=[],
        )

    async def _git_status(self) -> OrchestratorResult:
        git = WorkspaceGitService(self._settings)
        status = git.status()
        if not status.is_git_repo:
            return OrchestratorResult(
                executed=True,
                action=IncidentAction.GIT_STATUS.value,
                summary=status.warning or "The connected workspace is not a git repository.",
            )
        diff = git.diff()
        changed_count = len(status.files)
        changed_label = f"{changed_count} changed file{'s' if changed_count != 1 else ''}"
        summary = (
            f"Git branch {status.branch or 'unknown'} has {changed_label}."
            if status.dirty
            else f"Git branch {status.branch or 'unknown'} is clean."
        )
        status_lines = "\n".join(f"{item.status} {item.path}" for item in status.files)
        return OrchestratorResult(
            executed=True,
            action=IncidentAction.GIT_STATUS.value,
            summary=summary,
            artifacts=[
                {
                    "type": "logs",
                    "title": "git status",
                    "subtitle": changed_label if status.dirty else "clean",
                }
            ],
            command_output=_truncate(diff.diff or status_lines, 800),
            files_changed=diff.files_changed,
        )

    async def _patch(self, workspace: str | None, transcript: str) -> OrchestratorResult:
        if self._github_workspace:
            try:
                original = WorkspaceCodeService(self._settings).read("app.py", max_chars=80_000).content
            except WorkspaceError:
                return OrchestratorResult(
                    executed=False,
                    action=IncidentAction.PATCH.value,
                    summary="Could not find app.py in the GitHub repository to patch.",
                )
            updated, llm_metadata = await self._generate_fix(original, "GitHub-direct patch request; local tests are unavailable.", transcript)
            if not updated or updated.strip() == original.strip():
                updated = _heuristic_health_fix(original)
                llm_metadata = {**llm_metadata, "fallback": True, "fallback_strategy": "heuristic_health_fix"}
            proposed_files = {"app.py": updated}
            diff = _unified_diff("app.py", original, updated)
            if not diff.strip():
                return OrchestratorResult(
                    executed=False,
                    action=IncidentAction.PATCH.value,
                    summary="I could not produce a meaningful GitHub patch from the request.",
                )
            return OrchestratorResult(
                executed=False,
                action=IncidentAction.PATCH.value,
                summary="I prepared a GitHub patch for app.py. Review the diff and approve it to open a PR.",
                files_changed=["app.py"],
                artifacts=[{"type": "pr", "title": "1 file patch proposal", "subtitle": "waiting for approval"}],
                command_output="Local tests skipped in GitHub-direct mode.",
                pending_approval=True,
                approval={
                    "kind": "patch",
                    "status": "pending_approval",
                    "diff": diff,
                    "test_command": "GitHub PR checks",
                    "proposed_files": ["app.py"],
                    "source": "workspace_orchestrator",
                    "llm": llm_metadata,
                },
                approval_payload={"kind": "patch", "files": proposed_files, "test_command": "GitHub PR checks"},
            )
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
        updated, llm_metadata = await self._generate_fix(original, failure, transcript)
        if not updated or updated.strip() == original.strip():
            updated = _heuristic_health_fix(original)
            llm_metadata = {**llm_metadata, "fallback": True, "fallback_strategy": "heuristic_health_fix"}

        proposed_files = {"app.py": updated}
        if _requests_docs_update(transcript):
            try:
                readme = read_file("README.md", configured=workspace)
                proposed_readme = _update_readme_for_health(readme)
                if proposed_readme.strip() != readme.strip():
                    proposed_files["README.md"] = proposed_readme
            except WorkspaceError:
                pass

        diff = "".join(
            _unified_diff(path, original if path == "app.py" else read_file(path, configured=workspace), content)
            for path, content in proposed_files.items()
        )
        if not diff.strip():
            return OrchestratorResult(
                executed=False,
                action=IncidentAction.PATCH.value,
                summary="I could not produce a meaningful patch from the failing test output.",
                command_output=_truncate(failure, 600),
            )
        changed_files = list(proposed_files.keys())
        file_label = ", ".join(changed_files)

        return OrchestratorResult(
            executed=False,
            action=IncidentAction.PATCH.value,
            summary=f"I prepared a patch for {file_label}. Review the diff and approve it before I write to the workspace.",
            files_changed=changed_files,
            artifacts=[
                {
                    "type": "pr",
                    "title": f"{len(changed_files)} file patch proposal",
                    "subtitle": "waiting for approval",
                }
            ],
            command_output=_truncate(failure, 600),
            pending_approval=True,
            approval={
                "kind": "patch",
                "status": "pending_approval",
                "diff": diff,
                "test_command": "python -m pytest -q",
                "proposed_files": changed_files,
                "source": "workspace_orchestrator",
                "llm": llm_metadata,
            },
            approval_payload={
                "kind": "patch",
                "files": proposed_files,
                "test_command": "python -m pytest -q",
            },
        )

    async def _general_workspace_query(self, workspace: str | None, transcript: str) -> OrchestratorResult:
        lower = transcript.lower()
        if any(word in lower for word in ("fix", "patch", "health", "broken", "failing")):
            return await self._patch(workspace, transcript)
        if any(word in lower for word in ("test", "pytest")):
            return await self._run_tests(workspace)
        return await self._investigate(workspace, IncidentAction.INVESTIGATE)

    async def _generate_fix(self, source: str, failure: str, transcript: str) -> tuple[str | None, dict[str, Any]]:
        metadata: dict[str, Any] = {
            "provider": self._settings.llm_provider,
            "purpose": "patch_generation",
        }
        if self._settings.llm_provider == "mock":
            return None, {**metadata, "model": "local-heuristic", "fallback": True}
        try:
            response = await get_llm_runtime(self._settings).generate(
                LLMRequest(
                    purpose="patch_generation",
                    response_format="text",
                    temperature=0.1,
                    max_tokens=4000,
                    messages=[
                        LLMMessage(
                            role="system",
                            content=(
                                "You fix Python FastAPI code in a sandbox repo. "
                                "Return ONLY the complete updated app.py file contents. No markdown fences."
                            ),
                        ),
                        LLMMessage(
                            role="user",
                            content=(
                                f"User request: {transcript}\n\n"
                                f"Test failure:\n{failure}\n\n"
                                f"Current app.py:\n{source}"
                            ),
                        ),
                    ],
                    metadata={"tool_policy": "approval_required"},
                )
            )
        except Exception as exc:
            return None, {**metadata, "fallback": True, "error": exc.__class__.__name__}

        content = response.content.strip()
        content = re.sub(r"^```(?:python)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        return content.strip() or None, {
            **metadata,
            "provider": response.provider,
            "model": response.model,
            "usage": response.usage,
            "fallback": False,
        }


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


def _requests_docs_update(transcript: str) -> bool:
    lower = transcript.lower()
    return any(word in lower for word in ("doc", "docs", "readme", "document"))


def _update_readme_for_health(source: str) -> str:
    if "GET /health" in source and "status" in source:
        return source
    addition = "\n\n## Health check\n\n- `GET /health` returns `{\"status\": \"ok\"}`.\n"
    return source.rstrip() + addition


def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _prepare_workspace(workspace: str | None) -> None:
    root = resolve_configured_workspace(workspace) if workspace else None
    if root is None:
        return
    requirements = root / "requirements.txt"
    if requirements.is_file():
        run_command("python -m pip install -q -r requirements.txt", configured=workspace)


def _contextual_transcript(transcript: str, command: NormalizedCommand) -> str:
    resolved = command.parameters.get("resolved_context")
    if not isinstance(resolved, dict) or not resolved.get("text"):
        return transcript
    return f"{transcript}\n\nRecent meeting context: {resolved['text']}"


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
