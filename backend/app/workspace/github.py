from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from app.config import Settings
from app.redaction import redact_sensitive_text
from app.workspace.github_auth import stored_github_token
from app.workspace.models import (
    CodeQueryResponse,
    GitDiffResponse,
    GitStatusResponse,
    WorkspaceFile,
    WorkspaceFileResponse,
    WorkspaceSearchMatch,
    WorkspaceSearchResponse,
    WorkspaceTreeResponse,
)
from app.workspace.tools import WorkspaceError, get_workspace_root

CommandRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[str]]

GITHUB_API = "https://api.github.com"
TEXT_EXTENSIONS = {".css", ".html", ".ini", ".js", ".json", ".jsx", ".md", ".py", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml"}
SKIP_DIRS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", ".voiceops_cache", "__pycache__", "build", "dist", "node_modules", "target"}
SKIP_SUFFIXES = {".lock", ".log", ".map", ".pyc", ".sqlite", ".sqlite3", ".sqlite3-shm", ".sqlite3-wal"}
LANGUAGE_BY_EXT = {
    ".css": "css",
    ".html": "html",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".py": "python",
    ".sh": "shell",
    ".sql": "sql",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def clone_github_repo(remote_url: str, target: Path, *, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """Clone with local gh auth when available; otherwise use non-interactive git."""
    gh_command = _gh_clone_command(remote_url, target)
    if gh_command and _gh_authenticated_for_clone():
        return _run_clone_command(gh_command, timeout)
    return _run_clone_command(["git", "clone", remote_url, str(target)], timeout)


def is_github_workspace(value: str | None) -> bool:
    return _github_repo_from_url(value) is not None


def github_workspace_metadata(settings: Settings, remote_url: str | None = None, *, fetch: bool = False) -> dict[str, Any]:
    owner, repo = _require_github_repo(remote_url or settings.voiceops_workspace)
    branch = "main"
    name = repo
    web_url = f"https://github.com/{owner}/{repo}"
    if fetch:
        try:
            data = _github_api(settings, "GET", f"/repos/{owner}/{repo}")
            branch = str(data.get("default_branch") or branch)
            name = str(data.get("name") or name)
            web_url = str(data.get("html_url") or web_url)
        except WorkspaceError:
            pass
    return {
        "owner": owner,
        "repo": repo,
        "name": name,
        "branch": branch,
        "remote_url": f"https://github.com/{owner}/{repo}",
        "web_url": web_url,
    }


def github_status(settings: Settings) -> GitStatusResponse:
    meta = github_workspace_metadata(settings, fetch=True)
    return GitStatusResponse(
        is_git_repo=True,
        branch=meta["branch"],
        dirty=False,
        files=[],
        warning="Connected directly to GitHub; local working tree status is unavailable.",
    )


def github_diff(settings: Settings) -> GitDiffResponse:
    return GitDiffResponse(branch=github_workspace_metadata(settings, fetch=True)["branch"], diff="", files_changed=[])


def github_tree(settings: Settings, *, limit: int = 120) -> WorkspaceTreeResponse:
    meta = github_workspace_metadata(settings, fetch=True)
    items = _repo_tree(settings, meta["owner"], meta["repo"], meta["branch"])
    files: list[WorkspaceFile] = []
    total = 0
    for item in items:
        path = str(item.get("path") or "")
        if item.get("type") != "blob" or _skip_remote_path(path):
            continue
        total += 1
        if len(files) >= limit:
            continue
        files.append(
            WorkspaceFile(
                path=path,
                size=int(item.get("size") or 0),
                language=_language(path),
            )
        )
    return WorkspaceTreeResponse(root=meta["name"], files=files, total_files=total, truncated=total > len(files), mode="github")


def github_read_file(settings: Settings, path: str, *, max_chars: int = 12000) -> WorkspaceFileResponse:
    meta = github_workspace_metadata(settings, fetch=True)
    data = _github_api(
        settings,
        "GET",
        f"/repos/{meta['owner']}/{meta['repo']}/contents/{_url_path(path)}?ref={_url_path(meta['branch'])}",
    )
    if data.get("type") != "file":
        raise WorkspaceError(f"Not a GitHub file: {path}")
    raw = base64.b64decode(str(data.get("content") or "").encode("utf-8")).decode("utf-8", errors="replace")
    truncated = len(raw) > max_chars
    return WorkspaceFileResponse(
        path=path,
        content=raw[:max_chars],
        size=int(data.get("size") or len(raw)),
        language=_language(path),
        truncated=truncated,
    )


def github_search(settings: Settings, query: str, *, limit: int = 20) -> WorkspaceSearchResponse:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_./-]{2,}", query) if len(term) > 1]
    if not terms:
        return WorkspaceSearchResponse(query=query, matches=[], mode="github")
    meta = github_workspace_metadata(settings, fetch=True)
    matches: list[WorkspaceSearchMatch] = []
    # ponytail: bounded API scan; switch to GitHub code search or a remote index when large repos need full coverage.
    for item in _repo_tree(settings, meta["owner"], meta["repo"], meta["branch"]):
        if len(matches) >= limit:
            break
        path = str(item.get("path") or "")
        size = int(item.get("size") or 0)
        if item.get("type") != "blob" or _skip_remote_path(path) or Path(path).suffix.lower() not in TEXT_EXTENSIONS or size > 200_000:
            continue
        try:
            content = github_read_file(settings, path, max_chars=200_000).content
        except WorkspaceError:
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            lower = line.lower()
            if not any(term in lower or term in path.lower() for term in terms):
                continue
            matches.append(WorkspaceSearchMatch(path=path, line=line_no, snippet=" ".join(line.split())[:240], score=_score(line, terms, path)))
            if len(matches) >= limit:
                break
    matches.sort(key=lambda item: item.score, reverse=True)
    return WorkspaceSearchResponse(query=query, matches=matches, mode="github")


def github_query(settings: Settings, question: str, *, limit: int = 8) -> CodeQueryResponse:
    matches = github_search(settings, question, limit=limit).matches
    if not matches:
        return CodeQueryResponse(answer="I could not find matching code references in the connected GitHub repository.", references=[], mode="github")
    files = list(dict.fromkeys(match.path for match in matches))
    first = matches[0]
    return CodeQueryResponse(
        answer=(
            f"I found {len(matches)} relevant GitHub code reference"
            f"{'' if len(matches) == 1 else 's'} across {len(files)} file"
            f"{'' if len(files) == 1 else 's'}. Start with {first.path}:{first.line}: {first.snippet}"
        ),
        references=matches,
        mode="github",
    )


def create_github_patch_pull_request(
    settings: Settings,
    action: Any,
    *,
    files: dict[str, str],
    approved_by: str,
    base_branch: str | None = None,
) -> dict[str, Any]:
    if not settings.github_pr_creation_enabled:
        raise WorkspaceError("Real GitHub PR creation is disabled. Set GITHUB_PR_CREATION_ENABLED=true to enable it.")
    if not files:
        raise WorkspaceError("Approval proposal has no files to publish.")

    meta = github_workspace_metadata(settings, fetch=True)
    base = base_branch or meta["branch"]
    allowed = _allowed_base_branches(settings)
    if base not in allowed:
        raise WorkspaceError(f"Base branch '{base}' is not allowed. Allowed: {', '.join(sorted(allowed)) or '(none)'}.")

    owner = meta["owner"]
    repo = meta["repo"]
    branch = _remote_branch_name(action.id, action.summary)
    title = _title(None, action.summary)
    body = _body(None, action, {"files_changed": sorted(files), "branch_name": branch})
    base_ref = _github_api(settings, "GET", f"/repos/{owner}/{repo}/git/ref/heads/{_url_path(base)}")
    base_sha = str((base_ref.get("object") or {}).get("sha") or "")
    if not base_sha:
        raise WorkspaceError(f"Could not resolve GitHub base branch '{base}'.")
    _create_branch_if_missing(settings, owner, repo, branch, base_sha)

    commit_sha = None
    commit_message = _commit_message(None, action.summary)
    for path, content in sorted(files.items()):
        commit_sha = _put_file(settings, owner, repo, branch, path, str(content), commit_message)

    pr = _github_api(
        settings,
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        {
            "title": title,
            "body": body,
            "head": branch,
            "base": base,
            "draft": bool(settings.github_pr_create_draft),
        },
    )
    return {
        "provider": "github",
        "mode": "created",
        "ready": True,
        "title": title,
        "body": body,
        "base_branch": base,
        "head_branch": branch,
        "remote_url": meta["remote_url"],
        "web_url": meta["web_url"],
        "commit_sha": commit_sha,
        "files_changed": sorted(files),
        "pull_request_url": pr.get("html_url"),
        "pull_request_number": pr.get("number"),
        "draft": bool(pr.get("draft", settings.github_pr_create_draft)),
        "approved_by": approved_by,
        "commands": [],
        "command_results": [
            {
                "command": "GitHub API",
                "exit_code": 0,
                "stdout": "Created remote branch, committed files, and opened pull request.",
                "stderr": "",
            }
        ],
    }


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

    if is_github_workspace(settings.voiceops_workspace):
        git = dict((action.approval or {}).get("git") or {})
        pr = dict((action.approval or {}).get("pull_request") or {})
        meta = github_workspace_metadata(settings)
        pr_title = _title(title, action.summary)
        pr_body = _body(body, action, git)
        allowed_base_branches = _allowed_base_branches(settings)
        blockers = []
        if base_branch not in allowed_base_branches:
            blockers.append(f"Base branch '{base_branch}' is not allowed. Allowed: {', '.join(sorted(allowed_base_branches)) or '(none)'}.")
        if not pr.get("url") and not git.get("commit_sha"):
            blockers.append("Approve the patch to create the GitHub branch and pull request.")
        return {
            "ready": not blockers,
            "provider": "github",
            "mode": pr.get("mode") or "dry_run",
            "action_id": action.id,
            "title": pr.get("title") or pr_title,
            "body": pr_body,
            "base_branch": pr.get("base_branch") or base_branch,
            "head_branch": pr.get("head_branch") or git.get("branch_name"),
            "remote_url": meta["remote_url"],
            "web_url": meta["web_url"],
            "commit_sha": pr.get("commit_sha") or git.get("commit_sha"),
            "files_changed": git.get("files_changed") or action.files_changed,
            "blockers": blockers,
            "warnings": ["GitHub-direct work uses PR checks instead of local test execution."],
            "preflight": {
                "real_creation_enabled": settings.github_pr_creation_enabled,
                "workspace_source": settings.voiceops_workspace_source,
                "workspace_path": None,
                "remote_present": True,
                "remote_is_github": True,
                "base_branch_allowed": base_branch in allowed_base_branches,
                "commit_present": bool(pr.get("commit_sha") or git.get("commit_sha")),
                "real_creation_ready": settings.github_pr_creation_enabled and not blockers,
                "real_creation_blockers": ([] if settings.github_pr_creation_enabled else ["Real GitHub PR creation is disabled."]) + blockers,
            },
            "commands": [],
            "pull_request_url": pr.get("url"),
            "pull_request_number": pr.get("number"),
            "draft": pr.get("draft"),
        }

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

    if is_github_workspace(settings.voiceops_workspace):
        pr = dict((action.approval or {}).get("pull_request") or {})
        if pr.get("url"):
            return build_pull_request_plan(settings, action, title=title, body=body, base_branch=base_branch, runner=runner)
        raise WorkspaceError("GitHub-direct pull requests are created when the patch is approved.")

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


def _github_repo_from_url(value: str | None) -> tuple[str, str] | None:
    text = (value or "").strip()
    patterns = [
        r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$",
        r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group("owner"), match.group("repo")
    return None


def _require_github_repo(value: str | None) -> tuple[str, str]:
    repo = _github_repo_from_url(value)
    if repo is None:
        raise WorkspaceError("Workspace is not a GitHub repository URL.")
    return repo


def _github_api(settings: Settings, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"{GITHUB_API}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "VoiceOps",
            "X-GitHub-Api-Version": "2022-11-28",
            **_github_auth_header(settings),
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = _github_error_detail(exc)
        raise WorkspaceError(f"GitHub API failed ({exc.code}): {detail}") from exc
    except error.URLError as exc:
        raise WorkspaceError(f"GitHub API unavailable: {exc.reason}") from exc
    return json.loads(raw or "{}")


def _github_auth_header(settings: Settings) -> dict[str, str]:
    token = settings.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or stored_github_token(settings)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _github_error_detail(exc: error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8")
        data = json.loads(raw)
        return redact_sensitive_text(str(data.get("message") or raw))[:500]
    except Exception:
        return redact_sensitive_text(str(exc.reason or "request failed"))[:500]


def _repo_tree(settings: Settings, owner: str, repo: str, branch: str) -> list[dict[str, Any]]:
    data = _github_api(settings, "GET", f"/repos/{owner}/{repo}/git/trees/{_url_path(branch)}?recursive=1")
    tree = data.get("tree") or []
    return tree if isinstance(tree, list) else []


def _skip_remote_path(path: str) -> bool:
    parts = tuple(part for part in path.split("/") if part)
    if not parts or any(part in SKIP_DIRS for part in parts):
        return True
    if parts[-1].startswith(".env") or Path(path).suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def _language(path: str) -> str | None:
    return LANGUAGE_BY_EXT.get(Path(path).suffix.lower())


def _score(text: str, terms: list[str], path: str) -> int:
    lower = text.lower()
    path_lower = path.lower()
    return sum(3 for term in terms if term in path_lower) + sum(1 for term in terms if term in lower)


def _url_path(value: str) -> str:
    from urllib.parse import quote

    return quote(value.strip().strip("/"), safe="/")


def _create_branch_if_missing(settings: Settings, owner: str, repo: str, branch: str, base_sha: str) -> None:
    try:
        _github_api(settings, "GET", f"/repos/{owner}/{repo}/git/ref/heads/{_url_path(branch)}")
        return
    except WorkspaceError as exc:
        if "(404)" not in str(exc):
            raise
    _github_api(settings, "POST", f"/repos/{owner}/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})


def _put_file(settings: Settings, owner: str, repo: str, branch: str, path: str, content: str, message: str) -> str:
    sha = None
    try:
        current = _github_api(settings, "GET", f"/repos/{owner}/{repo}/contents/{_url_path(path)}?ref={_url_path(branch)}")
        sha = current.get("sha")
    except WorkspaceError as exc:
        if "(404)" not in str(exc):
            raise
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    # ponytail: one commit per file; switch to git trees if multi-file PR history gets too noisy.
    result = _github_api(settings, "PUT", f"/repos/{owner}/{repo}/contents/{_url_path(path)}", payload)
    return str(((result.get("commit") or {}).get("sha")) or "")


def _remote_branch_name(action_id: str, summary: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")[:32] or "patch"
    safe_action = re.sub(r"[^a-zA-Z0-9-]+", "-", action_id).strip("-")
    return f"voiceops/{safe_action}-{slug}"


def _commit_message(message: str | None, summary: str) -> str:
    text = " ".join((message or "").split()) or f"VoiceOps: {' '.join(summary.split())[:72]}"
    return text[:200] or "VoiceOps: approved patch"


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
