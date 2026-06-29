from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.redaction import redact_sensitive_text
from app.workspace.tools import WorkspaceError, get_workspace_root

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[str]]


def clone_github_repo(remote_url: str, target: Path, *, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """Clone with local gh auth when available; otherwise use non-interactive git."""
    gh_command = _gh_clone_command(remote_url, target)
    if gh_command and _gh_authenticated_for_clone():
        return _run_clone_command(gh_command, timeout)
    return _run_clone_command(["git", "clone", remote_url, str(target)], timeout)


def build_pull_request_plan(
    settings: Settings,
    action: Any,
    *,
    title: str | None = None,
    body: str | None = None,
    base_branch: str = "main",
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Build a safe GitHub PR plan without pushing or creating a PR."""

    try:
        root = get_workspace_root(settings.voiceops_workspace)
    except WorkspaceError as exc:
        git = dict((action.approval or {}).get("git") or {})
        pr_title = _title(title, action.summary)
        pr_body = _body(body, action, git)
        allowed_base_branches = _allowed_base_branches(settings)
        cli_available = _command_available(settings.github_pr_cli_path)
        auth_preflight = _github_cli_auth_preflight(settings, None, runner=runner)
        real_creation_blockers = _real_creation_blockers(
            settings,
            blockers=[str(exc)],
            cli_available=cli_available,
            auth_preflight=auth_preflight,
        )
        return {
            "ready": False,
            "provider": "github",
            "mode": "dry_run",
            "action_id": action.id,
            "title": pr_title,
            "body": pr_body,
            "base_branch": base_branch,
            "head_branch": git.get("branch_name"),
            "remote_url": None,
            "web_url": None,
            "commit_sha": git.get("commit_sha"),
            "files_changed": git.get("committed_files") or git.get("files_changed") or action.files_changed,
            "blockers": [str(exc)],
            "warnings": ["Real GitHub PR creation is disabled; this is a dry-run plan only."]
            if not settings.github_pr_creation_enabled
            else [],
            "preflight": {
                "real_creation_enabled": settings.github_pr_creation_enabled,
                "workspace_source": settings.voiceops_workspace_source,
                "workspace_path": None,
                "github_cli_path": settings.github_pr_cli_path,
                "github_cli_available": cli_available,
                **auth_preflight,
                "allowed_base_branches": sorted(allowed_base_branches),
                "base_branch_allowed": base_branch in allowed_base_branches if allowed_base_branches else False,
                "head_branch_present": bool(git.get("branch_name")),
                "remote_present": False,
                "remote_is_github": False,
                "commit_present": bool(git.get("commit_sha")),
                "real_creation_ready": False,
                "real_creation_blockers": real_creation_blockers,
            },
            "commands": [],
        }
    git = dict((action.approval or {}).get("git") or {})
    head_branch = git.get("branch_name") or _current_branch(root)
    remote_url = _origin_remote(root)
    web_url = _github_web_url(remote_url)
    pr_title = _title(title, action.summary)
    pr_body = _body(body, action, git)
    blockers: list[str] = []
    warnings: list[str] = []
    allowed_base_branches = _allowed_base_branches(settings)
    cli_available = _command_available(settings.github_pr_cli_path)
    auth_preflight = _github_cli_auth_preflight(settings, root, runner=runner)

    if not _is_git_repo(root):
        blockers.append("Workspace is not a git repository.")
    if not allowed_base_branches:
        blockers.append("No GitHub PR base branches are allowed.")
    elif base_branch not in allowed_base_branches:
        blockers.append(
            f"Base branch '{base_branch}' is not allowed. Allowed: {', '.join(sorted(allowed_base_branches))}."
        )
    if not remote_url:
        blockers.append("Git remote 'origin' is not configured.")
    elif not web_url:
        blockers.append("Git remote 'origin' is not a GitHub repository.")
    if not head_branch:
        blockers.append("Approved action has no branch to publish.")
    elif head_branch == base_branch:
        blockers.append("Head branch must be different from the base branch.")
    if not git.get("commit_sha"):
        blockers.append("Commit the approved patch before opening a pull request.")
    if not settings.github_pr_creation_enabled:
        warnings.append("Real GitHub PR creation is disabled; this is a dry-run plan only.")

    commands = []
    if head_branch:
        commands.append(f"git push -u origin {_shell_quote(head_branch)}")
        commands.append(
            "gh pr create "
            f"--base {_shell_quote(base_branch)} "
            f"--head {_shell_quote(head_branch)} "
            f"--title {_shell_quote(pr_title)} "
            f"--body {_shell_quote(pr_body)}"
        )

    real_creation_blockers = _real_creation_blockers(
        settings,
        blockers=blockers,
        cli_available=cli_available,
        auth_preflight=auth_preflight,
    )

    return {
        "ready": not blockers,
        "provider": "github",
        "mode": "dry_run",
        "action_id": action.id,
        "title": pr_title,
        "body": pr_body,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "remote_url": remote_url,
        "web_url": web_url,
        "commit_sha": git.get("commit_sha"),
        "files_changed": git.get("committed_files") or git.get("files_changed") or action.files_changed,
        "blockers": blockers,
        "warnings": warnings,
        "preflight": {
            "real_creation_enabled": settings.github_pr_creation_enabled,
            "workspace_source": settings.voiceops_workspace_source,
            "workspace_path": str(root),
            "github_cli_path": settings.github_pr_cli_path,
            "github_cli_available": cli_available,
            **auth_preflight,
            "allowed_base_branches": sorted(allowed_base_branches),
            "base_branch_allowed": base_branch in allowed_base_branches if allowed_base_branches else False,
            "head_branch_present": bool(head_branch),
            "remote_present": bool(remote_url),
            "remote_is_github": bool(web_url),
            "commit_present": bool(git.get("commit_sha")),
            "real_creation_ready": not real_creation_blockers,
            "real_creation_blockers": real_creation_blockers,
        },
        "commands": commands,
    }


def create_pull_request(
    settings: Settings,
    action: Any,
    *,
    title: str | None = None,
    body: str | None = None,
    base_branch: str = "main",
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Push the approved action branch and create a GitHub PR via gh CLI."""

    if not settings.github_pr_creation_enabled:
        raise WorkspaceError("Real GitHub PR creation is disabled. Set GITHUB_PR_CREATION_ENABLED=true to enable it.")
    allowed = {branch.strip() for branch in settings.github_pr_allowed_base_branches if branch.strip()}
    if base_branch not in allowed:
        raise WorkspaceError(
            f"Base branch '{base_branch}' is not allowed. Allowed: {', '.join(sorted(allowed)) or '(none)'}."
        )

    command_runner = runner or _run_command
    plan = build_pull_request_plan(
        settings,
        action,
        title=title,
        body=body,
        base_branch=base_branch,
        runner=command_runner,
    )
    if not plan["ready"]:
        raise WorkspaceError("; ".join(plan["blockers"]) or "Pull request plan is not ready.")
    root = get_workspace_root(settings.voiceops_workspace)
    head_branch = str(plan["head_branch"] or "")
    pr_title = str(plan["title"])
    pr_body = str(plan["body"])
    timeout = float(settings.github_pr_command_timeout_seconds)

    auth = command_runner([settings.github_pr_cli_path, "auth", "status"], root, timeout)
    if auth.returncode != 0:
        raise WorkspaceError(_clean_command_error(auth) or "GitHub CLI is not authenticated.")

    push_args = ["git", "push", "-u", "origin", head_branch]
    push = command_runner(push_args, root, timeout)
    if push.returncode != 0:
        raise WorkspaceError(_clean_command_error(push) or "Could not push branch to origin.")

    pr_args = [
        settings.github_pr_cli_path,
        "pr",
        "create",
        "--base",
        base_branch,
        "--head",
        head_branch,
        "--title",
        pr_title,
        "--body",
        pr_body,
    ]
    if settings.github_pr_create_draft:
        pr_args.append("--draft")
    created = command_runner(pr_args, root, timeout)
    if created.returncode != 0:
        raise WorkspaceError(_clean_command_error(created) or "Could not create GitHub pull request.")

    pull_request_url = _extract_pull_request_url(created.stdout) or _extract_pull_request_url(created.stderr)
    number = _extract_pull_request_number(pull_request_url)
    return {
        **plan,
        "mode": "created",
        "ready": True,
        "pull_request_url": pull_request_url,
        "pull_request_number": number,
        "draft": settings.github_pr_create_draft,
        "commands": [
            " ".join(_display_arg(item) for item in push_args),
            " ".join(_display_arg(item) for item in pr_args),
        ],
        "command_results": [
            _command_result("gh auth status", auth),
            _command_result("git push", push),
            _command_result("gh pr create", created),
        ],
    }


def _is_git_repo(root: Path) -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _current_branch(root: Path) -> str | None:
    value = _git(root, "branch", "--show-current").stdout.strip()
    return value or None


def _origin_remote(root: Path) -> str | None:
    value = _git(root, "config", "--get", "remote.origin.url").stdout.strip()
    return value or None


def _allowed_base_branches(settings: Settings) -> set[str]:
    return {branch.strip() for branch in settings.github_pr_allowed_base_branches if branch.strip()}


def _command_available(command: str) -> bool:
    if not command:
        return False
    if "/" in command:
        path = Path(command).expanduser()
        return path.exists() and path.is_file()
    return shutil.which(command) is not None


def _real_creation_blockers(
    settings: Settings,
    *,
    blockers: list[str],
    cli_available: bool,
    auth_preflight: dict[str, Any],
) -> list[str]:
    result = list(blockers)
    if not settings.github_pr_creation_enabled:
        result.append("Real GitHub PR creation is disabled.")
    if not cli_available:
        result.append("GitHub CLI is not installed or not on PATH.")
    elif not auth_preflight.get("github_cli_authenticated"):
        result.append(auth_preflight.get("github_cli_auth_detail") or "GitHub CLI is not authenticated.")
    return result


def _github_cli_auth_preflight(
    settings: Settings,
    root: Path | None,
    *,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    if not _command_available(settings.github_pr_cli_path):
        return {
            "github_cli_authenticated": False,
            "github_cli_auth_detail": "GitHub CLI is not installed or not on PATH.",
        }
    if root is None:
        return {
            "github_cli_authenticated": False,
            "github_cli_auth_detail": "Workspace unavailable; GitHub CLI auth was not checked.",
        }
    result = (runner or _run_command)(
        [settings.github_pr_cli_path, "auth", "status"],
        root,
        min(float(settings.github_pr_command_timeout_seconds), 15.0),
    )
    detail = _clean_command_error(result) or ("GitHub CLI is authenticated." if result.returncode == 0 else "GitHub CLI is not authenticated.")
    return {
        "github_cli_authenticated": result.returncode == 0,
        "github_cli_auth_detail": detail.replace("\n", " ")[:300],
    }


def _github_web_url(remote_url: str | None) -> str | None:
    if not remote_url:
        return None
    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, remote_url.strip())
        if match:
            return f"https://github.com/{match.group('owner')}/{match.group('repo')}"
    return None


def _gh_clone_command(remote_url: str, target: Path) -> list[str] | None:
    if not remote_url.startswith("https://github.com/"):
        return None
    match = re.match(r"^https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$", remote_url.strip())
    if not match:
        return None
    return ["gh", "repo", "clone", f"{match.group('owner')}/{match.group('repo')}", str(target)]


def _gh_authenticated_for_clone() -> bool:
    if not _command_available("gh"):
        return False
    return _run_clone_command(["gh", "auth", "status"], 15).returncode == 0


def _run_clone_command(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, stdout="", stderr=f"Command not found: {args[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, stdout="", stderr=f"Command timed out: {args[0]}")


def _title(value: str | None, summary: str) -> str:
    text = " ".join((value or summary or "VoiceOps approved patch").split())
    return redact_sensitive_text(text)[:120] or "VoiceOps approved patch"


def _body(value: str | None, action: Any, git: dict[str, Any]) -> str:
    if value and value.strip():
        return redact_sensitive_text(value.strip())[:5000]
    files = git.get("committed_files") or git.get("files_changed") or action.files_changed or []
    lines = [
        f"VoiceOps action: {action.id}",
        f"Requester: {action.requested_by_name}",
        f"Summary: {action.summary}",
    ]
    if files:
        lines.append(f"Files: {', '.join(files)}")
    if git.get("commit_sha"):
        lines.append(f"Commit: {git['commit_sha']}")
    if git.get("branch_name"):
        lines.append(f"Branch: {git['branch_name']}")
    return redact_sensitive_text("\n".join(lines))[:5000]


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _display_arg(value: str) -> str:
    if re.search(r"\s|['\"$`;&|()<>]", value):
        return _shell_quote(value)
    return value


def _extract_pull_request_url(output: str) -> str | None:
    match = re.search(r"https://github\.com/[^\s]+/pull/\d+", output or "")
    return match.group(0).rstrip(".,") if match else None


def _extract_pull_request_number(url: str | None) -> int | None:
    if not url:
        return None
    match = re.search(r"/pull/(\d+)", url)
    return int(match.group(1)) if match else None


def _clean_command_error(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return _sanitize_command_text(text)[-1000:]


def _command_result(label: str, result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": label,
        "exit_code": result.returncode,
        "stdout": _sanitize_command_text(result.stdout or "")[-1000:],
        "stderr": _sanitize_command_text(result.stderr or "")[-1000:],
    }


def _sanitize_command_text(text: str) -> str:
    return redact_sensitive_text(text)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_command(args: list[str], root: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, stdout="", stderr=f"Command not found: {args[0]}")
