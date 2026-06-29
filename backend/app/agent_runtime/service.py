from __future__ import annotations

import difflib
import fnmatch
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.workspace.tools import WorkspaceError, resolve_configured_workspace, validate_command


RUNTIME_IGNORES = (
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".voiceops_cache",
    "node_modules",
    "dist",
    "build",
    ".env",
    ".env.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
)
MAX_OUTPUT_CHARS = 4000


class AgentRuntimeService:
    """Run agent validation/execution in a disposable workspace boundary."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_proposed_patch(
        self,
        *,
        proposed_files: dict[str, Any],
        command: str,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        source = resolve_configured_workspace(self._settings.voiceops_workspace)
        if source is None:
            return {
                "status": "skipped",
                "passed": False,
                "command": command,
                "detail": "No configured workspace was available for runtime validation.",
                "workspace": "temporary",
                "runtime": "isolated_temp_workspace",
            }
        if not proposed_files:
            return {
                "status": "skipped",
                "passed": False,
                "command": command,
                "detail": "No proposed files were available for runtime validation.",
                "workspace": "temporary",
                "runtime": "isolated_temp_workspace",
            }

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="voiceops-agent-runtime-") as temp_dir:
            target = (Path(temp_dir) / "workspace").resolve()
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(*RUNTIME_IGNORES))
            diff = self._apply_proposed_files(target, proposed_files)
            command_result = self._run_command(command, target, timeout_seconds)
            output = _truncate((command_result.get("stdout") or "") + (command_result.get("stderr") or ""))
            passed = command_result["exit_code"] == 0 and not command_result.get("timed_out")
            return {
                "status": "passed" if passed else "failed",
                "passed": passed,
                "command": command,
                "exit_code": command_result["exit_code"],
                "stdout": _truncate(command_result.get("stdout") or ""),
                "stderr": _truncate(command_result.get("stderr") or ""),
                "output": output,
                "workspace": "temporary",
                "workspace_path": str(target),
                "runtime": "isolated_temp_workspace",
                "files_changed": sorted(proposed_files),
                "diff": diff,
                "timed_out": bool(command_result.get("timed_out")),
                "blocked": bool(command_result.get("blocked")),
                "duration_ms": int((time.monotonic() - started) * 1000),
            }

    def run_read_only_command(
        self,
        *,
        command: str,
        timeout_seconds: int = 120,
        env: dict[str, str] | None = None,
        command_template: str | None = None,
        template_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        source = resolve_configured_workspace(self._settings.voiceops_workspace)
        if source is None:
            return {
                "status": "skipped",
                "passed": False,
                "command": command,
                "detail": "No configured workspace was available for runtime execution.",
                "workspace": "temporary",
                "runtime": "isolated_temp_workspace",
            }

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="voiceops-agent-runtime-") as temp_dir:
            target = (Path(temp_dir) / "workspace").resolve()
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(*RUNTIME_IGNORES))
            try:
                command_to_run = _render_command_template(
                    command_template,
                    workspace=target,
                    values=template_vars or {},
                ) if command_template else command
            except WorkspaceError as exc:
                return {
                    "status": "failed",
                    "passed": False,
                    "command": command_template or command,
                    "exit_code": 126,
                    "stdout": "",
                    "stderr": str(exc),
                    "output": str(exc),
                    "workspace": "temporary",
                    "workspace_path": str(target),
                    "runtime": "isolated_temp_workspace",
                    "files_changed": [],
                    "diff": "",
                    "timed_out": False,
                    "blocked": True,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            command_result = self._run_command(command_to_run, target, timeout_seconds, env=env)
            output = _truncate((command_result.get("stdout") or "") + (command_result.get("stderr") or ""))
            passed = command_result["exit_code"] == 0 and not command_result.get("timed_out")
            return {
                "status": "passed" if passed else "failed",
                "passed": passed,
                "command": command_to_run,
                "exit_code": command_result["exit_code"],
                "stdout": _truncate(command_result.get("stdout") or ""),
                "stderr": _truncate(command_result.get("stderr") or ""),
                "output": output,
                "workspace": "temporary",
                "workspace_path": str(target),
                "runtime": "isolated_temp_workspace",
                "files_changed": [],
                "diff": "",
                "timed_out": bool(command_result.get("timed_out")),
                "blocked": bool(command_result.get("blocked")),
                "duration_ms": int((time.monotonic() - started) * 1000),
            }

    def _apply_proposed_files(self, target: Path, proposed_files: dict[str, Any]) -> str:
        chunks: list[str] = []
        for relative_path, content in proposed_files.items():
            destination = (target / str(relative_path)).resolve()
            try:
                destination.relative_to(target)
            except ValueError as exc:
                raise WorkspaceError(f"Proposed file escapes workspace: {relative_path}") from exc
            before = destination.read_text(encoding="utf-8") if destination.exists() else ""
            after = str(content)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(after, encoding="utf-8")
            chunks.extend(
                difflib.unified_diff(
                    before.splitlines(),
                    after.splitlines(),
                    fromfile=f"a/{relative_path}",
                    tofile=f"b/{relative_path}",
                    lineterm="",
                )
            )
        return "\n".join(chunks)

    def _run_command(
        self,
        command: str,
        cwd: Path,
        timeout_seconds: int,
        *,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            validate_command(command)
            args = shlex.split(command)
            if not args:
                raise WorkspaceError("Command cannot be empty")
            result = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
                env=_runtime_command_env(env or {}),
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "exit_code": 124,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or f"Command timed out after {timeout_seconds}s.",
                "timed_out": True,
            }
        except WorkspaceError as exc:
            return {
                "exit_code": 126,
                "stdout": "",
                "stderr": str(exc),
                "timed_out": False,
                "blocked": True,
            }
        except ValueError as exc:
            return {
                "exit_code": 126,
                "stdout": "",
                "stderr": f"Invalid command: {exc}",
                "timed_out": False,
                "blocked": True,
            }


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _runtime_command_env(extra: dict[str, str]) -> dict[str, str]:
    """Return a minimal command environment without service-side secrets."""
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "VIRTUAL_ENV",
    }
    base = {
        key: value
        for key, value in os.environ.items()
        if key in allowed or key.startswith("LC_")
    }
    for key, value in extra.items():
        if value is not None:
            base[str(key)] = str(value)
    return base


def _render_command_template(template: str, *, workspace: Path, values: dict[str, str]) -> str:
    if "{workspace}" not in template:
        raise WorkspaceError("Command template must include {workspace}")
    replacements = {"workspace": shlex.quote(str(workspace))}
    replacements.update({key: shlex.quote(str(value)) for key, value in values.items()})
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def is_runtime_ignored(relative_path: Path) -> bool:
    return any(
        fnmatch.fnmatch(part, pattern) or fnmatch.fnmatch(relative_path.as_posix(), pattern)
        for part in relative_path.parts
        for pattern in RUNTIME_IGNORES
    )
