from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.agent_runtime import AgentRuntimeService
from app.agent_runtime.service import RUNTIME_IGNORES, is_runtime_ignored
from app.config import Settings
from app.external_agents.models import (
    ExternalAgentAuthMethod,
    ExternalAgentCredentialRecord,
    ExternalAgentProvider,
    ExternalAgentRunRequest,
)
from app.external_agents.service_labels import PROVIDER_LABELS
from app.redaction import redact_sensitive_text, redact_sensitive_value
from app.workspace.tools import WorkspaceError, resolve_configured_workspace


MAX_PATCH_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class ExternalAgentReadOnlyResult:
    status: str
    summary: str
    audit: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExternalAgentPatchResult:
    status: str
    summary: str
    files_changed: list[str] = field(default_factory=list)
    diff: str = ""
    proposed_files: dict[str, str] = field(default_factory=dict)
    test_command: str = "python -m pytest -q"
    audit: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ExternalAgentExecutionAdapter:
    """Adapter boundary for external provider execution."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run_read_only(
        self,
        *,
        provider_run_id: str,
        credential: ExternalAgentCredentialRecord,
        payload: dict,
        body: ExternalAgentRunRequest,
        model: str,
    ) -> ExternalAgentReadOnlyResult:
        if self._settings.external_agent_api_execution_enabled and credential.auth_method in {
            ExternalAgentAuthMethod.API_KEY,
            ExternalAgentAuthMethod.OAUTH,
        }:
            token = str(payload.get("api_key") or payload.get("access_token") or "").strip()
            if token:
                if body.provider == ExternalAgentProvider.CLAUDE:
                    return self._run_anthropic_messages_api(
                        token=token,
                        provider_run_id=provider_run_id,
                        body=body,
                        model=model,
                    )
                if body.provider == ExternalAgentProvider.CODEX:
                    return self._run_openai_responses_api(
                        token=token,
                        provider_run_id=provider_run_id,
                        body=body,
                        model=model,
                    )

        if credential.auth_method == ExternalAgentAuthMethod.LOCAL_CLI and self._settings.external_agent_cli_execution_enabled:
            command = str(payload.get("command") or "").strip()
            command_template = str(payload.get("command_template") or "").strip() or None
            if command:
                runtime = AgentRuntimeService(self._settings)
                runtime_result = runtime.run_read_only_command(
                    command=command,
                    timeout_seconds=self._settings.external_agent_cli_timeout_seconds,
                    env={
                        "VOICEOPS_AGENT_PROVIDER": body.provider.value,
                        "VOICEOPS_AGENT_MODEL": model,
                        "VOICEOPS_AGENT_MODE": body.mode.value,
                        "VOICEOPS_AGENT_PROMPT": body.prompt,
                        "VOICEOPS_PROVIDER_RUN_ID": provider_run_id,
                    },
                    command_template=command_template,
                    template_vars={
                        "prompt": body.prompt,
                        "model": model,
                        "provider_run_id": provider_run_id,
                        "provider": body.provider.value,
                    },
                )
                output = _compact_text(runtime_result.get("output") or runtime_result.get("detail") or "No output.")
                status = "completed" if runtime_result.get("passed") else "failed"
                return ExternalAgentReadOnlyResult(
                    status=status,
                    summary=f"{PROVIDER_LABELS[body.provider]} {body.mode.value} via local CLI with {model}: {output}",
                    audit={
                        "execution_mode": "local_cli",
                        "runtime": redact_sensitive_value(runtime_result),
                    },
                )

        return ExternalAgentReadOnlyResult(
            status="completed",
            summary=f"{PROVIDER_LABELS[body.provider]} completed {body.mode.value} with {model}: {body.prompt}",
            audit={
                "execution_mode": "mock_adapter",
                "detail": "Provider execution adapter is mocked until a CLI/API adapter is enabled.",
            },
        )

    def _run_anthropic_messages_api(
        self,
        *,
        token: str,
        provider_run_id: str,
        body: ExternalAgentRunRequest,
        model: str,
    ) -> ExternalAgentReadOnlyResult:
        url = f"{self._settings.anthropic_api_base_url.rstrip('/')}/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 1024,
            "system": _system_prompt(body.mode.value),
            "messages": [{"role": "user", "content": body.prompt}],
        }
        try:
            response = httpx.post(
                url,
                headers={
                    "x-api-key": token,
                    "anthropic-version": self._settings.anthropic_api_version,
                    "content-type": "application/json",
                },
                json=payload,
                timeout=self._settings.external_agent_api_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return _provider_error_result(body, model, provider_run_id, "anthropic_messages_api", exc)
        data = response.json()
        text = _anthropic_text(data)
        return ExternalAgentReadOnlyResult(
            status="completed",
            summary=f"{PROVIDER_LABELS[body.provider]} {body.mode.value} via Anthropic API with {model}: {text}",
            audit={
                "execution_mode": "anthropic_messages_api",
                "provider_run_id": provider_run_id,
                "model": data.get("model") or model,
                "response_id": data.get("id"),
                "stop_reason": data.get("stop_reason"),
                "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
            },
        )

    def _run_openai_responses_api(
        self,
        *,
        token: str,
        provider_run_id: str,
        body: ExternalAgentRunRequest,
        model: str,
    ) -> ExternalAgentReadOnlyResult:
        url = f"{self._settings.openai_api_base_url.rstrip('/')}/responses"
        payload = {
            "model": model,
            "instructions": _system_prompt(body.mode.value),
            "input": body.prompt,
        }
        try:
            response = httpx.post(
                url,
                headers={
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=self._settings.external_agent_api_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return _provider_error_result(body, model, provider_run_id, "openai_responses_api", exc)
        data = response.json()
        text = _openai_response_text(data)
        return ExternalAgentReadOnlyResult(
            status="completed",
            summary=f"{PROVIDER_LABELS[body.provider]} {body.mode.value} via OpenAI Responses API with {model}: {text}",
            audit={
                "execution_mode": "openai_responses_api",
                "provider_run_id": provider_run_id,
                "model": data.get("model") or model,
                "response_id": data.get("id"),
                "status": data.get("status"),
                "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
            },
        )


class ExternalCodingAgentAdapter:
    """Runs a coding agent in a disposable workspace and returns a patch proposal."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner or subprocess.run

    def propose_patch(
        self,
        *,
        provider_run_id: str,
        credential: ExternalAgentCredentialRecord,
        payload: dict,
        body: ExternalAgentRunRequest,
        model: str,
    ) -> ExternalAgentPatchResult:
        if credential.auth_method != ExternalAgentAuthMethod.LOCAL_CLI:
            return ExternalAgentPatchResult(
                status="unsupported",
                summary="Patch execution requires a local CLI credential for this provider.",
                audit={"execution_mode": "unsupported_auth_method"},
            )
        if not self._settings.external_agent_cli_execution_enabled:
            return ExternalAgentPatchResult(
                status="disabled",
                summary="External CLI patch execution is disabled.",
                audit={"execution_mode": "cli_patch_disabled"},
            )
        command = str(payload.get("command") or "").strip()
        command_template = str(payload.get("command_template") or "").strip()
        if not command:
            return ExternalAgentPatchResult(
                status="failed",
                summary="Local CLI credential did not include a command.",
                audit={"execution_mode": "local_cli_patch"},
                error="missing_command",
            )
        source = resolve_configured_workspace(self._settings.voiceops_workspace)
        if source is None:
            return ExternalAgentPatchResult(
                status="failed",
                summary="No configured workspace was available for patch proposal.",
                audit={"execution_mode": "local_cli_patch"},
                error="workspace_missing",
            )

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="voiceops-coding-agent-") as temp_dir:
            sandbox = (Path(temp_dir) / "workspace").resolve()
            shutil.copytree(source, sandbox, ignore=shutil.ignore_patterns(*RUNTIME_IGNORES))
            try:
                args = _patch_command_args(
                    body.provider,
                    command,
                    sandbox,
                    body.prompt,
                    model=model,
                    provider_run_id=provider_run_id,
                    command_template=command_template or None,
                )
            except (ValueError, WorkspaceError) as exc:
                return ExternalAgentPatchResult(
                    status="failed",
                    summary=f"{PROVIDER_LABELS[body.provider]} CLI command template is invalid.",
                    audit={
                        "execution_mode": "local_cli_patch",
                        "runtime": "isolated_temp_workspace",
                        "workspace": "temporary",
                        "env_policy": "minimal_external_agent_env",
                    },
                    error=str(exc),
                )
            env = _external_agent_env(
                {
                    "VOICEOPS_AGENT_PROVIDER": body.provider.value,
                    "VOICEOPS_AGENT_MODEL": model,
                    "VOICEOPS_AGENT_MODE": body.mode.value,
                    "VOICEOPS_AGENT_PROMPT": body.prompt,
                    "VOICEOPS_PROVIDER_RUN_ID": provider_run_id,
                    "VOICEOPS_WORKSPACE": str(sandbox),
                }
            )
            try:
                result = self._runner(
                    args,
                    cwd=sandbox,
                    capture_output=True,
                    text=True,
                    timeout=self._settings.external_agent_cli_timeout_seconds,
                    shell=False,
                    env=env,
                )
            except FileNotFoundError as exc:
                return ExternalAgentPatchResult(
                    status="unavailable",
                    summary=f"{PROVIDER_LABELS[body.provider]} CLI is not installed or not on PATH.",
                    audit={
                        "execution_mode": "local_cli_patch",
                        "command": _redacted_command(args),
                        "runtime": "isolated_temp_workspace",
                        "workspace": "temporary",
                        "env_policy": "minimal_external_agent_env",
                        "env_keys": sorted(env),
                    },
                    error=str(exc),
                )
            except subprocess.TimeoutExpired as exc:
                return ExternalAgentPatchResult(
                    status="failed",
                    summary=f"{PROVIDER_LABELS[body.provider]} CLI timed out while preparing a patch.",
                    audit={
                        "execution_mode": "local_cli_patch",
                        "command": _redacted_command(args),
                        "runtime": "isolated_temp_workspace",
                        "workspace": "temporary",
                        "env_policy": "minimal_external_agent_env",
                        "env_keys": sorted(env),
                        "timed_out": True,
                        "stdout": _compact_text(str(exc.stdout or ""), limit=MAX_PATCH_OUTPUT_CHARS),
                        "stderr": _compact_text(str(exc.stderr or ""), limit=MAX_PATCH_OUTPUT_CHARS),
                    },
                    error="timeout",
                )

            proposed = _collect_text_file_changes(source, sandbox)
            diff = _diff_proposed_files(source, proposed)
            files_changed = sorted(proposed)
            passed = result.returncode == 0
            if not passed:
                return ExternalAgentPatchResult(
                    status="failed",
                    summary=f"{PROVIDER_LABELS[body.provider]} CLI failed before producing an approvable patch.",
                    files_changed=files_changed,
                    diff=diff,
                    proposed_files=proposed,
                    audit={
                        "execution_mode": "local_cli_patch",
                        "command": _redacted_command(args),
                        "runtime": "isolated_temp_workspace",
                        "workspace": "temporary",
                        "env_policy": "minimal_external_agent_env",
                        "env_keys": sorted(env),
                        "exit_code": result.returncode,
                        "stdout": _compact_text(result.stdout or "", limit=MAX_PATCH_OUTPUT_CHARS),
                        "stderr": _compact_text(result.stderr or "", limit=MAX_PATCH_OUTPUT_CHARS),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                    error="cli_failed",
                )
            if not proposed:
                return ExternalAgentPatchResult(
                    status="failed",
                    summary=f"{PROVIDER_LABELS[body.provider]} CLI completed but did not produce any file changes.",
                    audit={
                        "execution_mode": "local_cli_patch",
                        "command": _redacted_command(args),
                        "runtime": "isolated_temp_workspace",
                        "workspace": "temporary",
                        "env_policy": "minimal_external_agent_env",
                        "env_keys": sorted(env),
                        "exit_code": result.returncode,
                        "stdout": _compact_text(result.stdout or "", limit=MAX_PATCH_OUTPUT_CHARS),
                        "stderr": _compact_text(result.stderr or "", limit=MAX_PATCH_OUTPUT_CHARS),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                    error="no_diff",
                )

            return ExternalAgentPatchResult(
                status="completed",
                summary=(
                    f"{PROVIDER_LABELS[body.provider]} prepared an approvable patch in a temporary workspace "
                    f"with {model}."
                ),
                files_changed=files_changed,
                diff=diff,
                proposed_files=proposed,
                audit={
                    "execution_mode": "local_cli_patch",
                    "command": _redacted_command(args),
                    "runtime": "isolated_temp_workspace",
                    "workspace": "temporary",
                    "env_policy": "minimal_external_agent_env",
                    "env_keys": sorted(env),
                    "exit_code": result.returncode,
                    "stdout": _compact_text(result.stdout or "", limit=MAX_PATCH_OUTPUT_CHARS),
                    "stderr": _compact_text(result.stderr or "", limit=MAX_PATCH_OUTPUT_CHARS),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )


def _system_prompt(mode: str) -> str:
    return (
        "You are an external coding teammate connected to VoiceOps. "
        "This run is read-only: explain, review, or test-plan only. "
        f"Mode: {mode}. Do not claim to have changed files."
    )


def _provider_error_result(
    body: ExternalAgentRunRequest,
    model: str,
    provider_run_id: str,
    execution_mode: str,
    exc: httpx.HTTPError,
) -> ExternalAgentReadOnlyResult:
    status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) and exc.response else None
    return ExternalAgentReadOnlyResult(
        status="failed",
        summary=f"{PROVIDER_LABELS[body.provider]} {body.mode.value} failed via {execution_mode} with {model}: {_compact_text(str(exc), limit=600)}",
        audit={
            "execution_mode": execution_mode,
            "provider_run_id": provider_run_id,
            "model": model,
            "error": _compact_text(str(exc), limit=600),
            "status_code": status_code,
        },
    )


def _anthropic_text(data: dict) -> str:
    blocks = data.get("content") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return ""
    parts = [
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return _compact_text(" ".join(parts))


def _openai_response_text(data: dict) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return _compact_text(direct)
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                parts.append(str(content.get("text") or ""))
    return _compact_text(" ".join(parts))


def _compact_text(text: str, *, limit: int = 2400) -> str:
    text = " ".join(_sanitize_output(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _sanitize_output(text: str) -> str:
    return redact_sensitive_text(text)


def _patch_command_args(
    provider: ExternalAgentProvider,
    command: str,
    sandbox: Path,
    prompt: str,
    *,
    model: str,
    provider_run_id: str,
    command_template: str | None = None,
) -> list[str]:
    if command_template:
        if "{workspace}" not in command_template:
            raise WorkspaceError("External agent command_template must include {workspace}")
        return shlex.split(
            _render_command_template(
                command_template,
                sandbox=sandbox,
                prompt=prompt,
                model=model,
                provider_run_id=provider_run_id,
                provider=provider,
            )
        )
    base = shlex.split(command)
    if not base:
        raise WorkspaceError("External agent command cannot be empty")
    executable = Path(base[0]).name
    if provider == ExternalAgentProvider.CODEX and executable == "codex" and len(base) == 1:
        return [
            *base,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(sandbox),
            prompt,
        ]
    return base


def _render_command_template(
    template: str,
    *,
    sandbox: Path,
    prompt: str,
    model: str,
    provider_run_id: str,
    provider: ExternalAgentProvider,
) -> str:
    replacements = {
        "workspace": shlex.quote(str(sandbox)),
        "prompt": shlex.quote(prompt),
        "model": shlex.quote(model),
        "provider_run_id": shlex.quote(provider_run_id),
        "provider": shlex.quote(provider.value),
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def _collect_text_file_changes(source: Path, sandbox: Path) -> dict[str, str]:
    proposed: dict[str, str] = {}
    for path in _iter_files(sandbox):
        relative = path.relative_to(sandbox).as_posix()
        source_path = source / relative
        try:
            after = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        before = source_path.read_text(encoding="utf-8") if source_path.exists() and source_path.is_file() else None
        if before != after:
            proposed[relative] = after
    return proposed


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if is_runtime_ignored(path.relative_to(root)):
            continue
        if path.is_file():
            files.append(path)
    return files


def _diff_proposed_files(source: Path, proposed: dict[str, str]) -> str:
    chunks: list[str] = []
    for relative_path in sorted(proposed):
        source_path = source / relative_path
        before = source_path.read_text(encoding="utf-8") if source_path.exists() and source_path.is_file() else ""
        after = proposed[relative_path]
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


def _redacted_command(args: list[str]) -> list[str]:
    redacted: list[str] = []
    for value in args:
        lower = value.lower()
        if "token" in lower or "key" in lower or _sanitize_output(value) != value:
            redacted.append("[redacted]")
        else:
            redacted.append(value)
    return redacted


def _external_agent_env(extra: dict[str, str]) -> dict[str, str]:
    """Return a minimal local CLI environment without service-side secrets."""
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
