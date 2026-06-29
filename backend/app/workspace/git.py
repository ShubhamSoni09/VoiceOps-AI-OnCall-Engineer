from __future__ import annotations

import re
import subprocess
from hashlib import sha1
from pathlib import Path

from app.cache import JsonTTLCache
from app.config import Settings
from app.workspace.models import GitDiffResponse, GitFileStatus, GitStatusResponse
from app.workspace.tools import WorkspaceError, get_workspace_root


class WorkspaceGitService:
    """Small git adapter for local branch/status/diff operations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache = JsonTTLCache(settings.voiceops_cache_path)

    def status(self, *, use_cache: bool = True) -> GitStatusResponse:
        root = get_workspace_root(self._settings.voiceops_workspace)
        cache_key = _cache_key(root, "status")
        source_token = _workspace_source_token(root)
        if use_cache:
            cached = self._cache.get(cache_key, source_token=source_token)
            if cached:
                return GitStatusResponse.model_validate(cached)
        if not _is_git_repo(root):
            response = GitStatusResponse(
                is_git_repo=False,
                warning=_git_repo_warning(root),
            )
            self._cache.set(
                cache_key,
                response.model_dump(mode="json"),
                ttl_seconds=self._settings.git_cache_ttl_seconds,
                source_token=source_token,
            )
            return response
        branch = _git(root, "branch", "--show-current", check=False).stdout.strip() or None
        short = _git(root, "status", "--short", "--", ".", check=False).stdout
        files = _parse_status(short)
        response = GitStatusResponse(
            is_git_repo=True,
            branch=branch,
            dirty=bool(files),
            files=files,
        )
        self._cache.set(
            cache_key,
            response.model_dump(mode="json"),
            ttl_seconds=self._settings.git_cache_ttl_seconds,
            source_token=source_token,
        )
        return response

    def diff(self, *, limit: int = 12000, use_cache: bool = True) -> GitDiffResponse:
        root = get_workspace_root(self._settings.voiceops_workspace)
        cache_key = _cache_key(root, f"diff:{limit}")
        source_token = _workspace_source_token(root)
        if use_cache:
            cached = self._cache.get(cache_key, source_token=source_token)
            if cached:
                return GitDiffResponse.model_validate(cached)
        if not _is_git_repo(root):
            response = GitDiffResponse(branch=None, diff="", files_changed=[])
            self._cache.set(
                cache_key,
                response.model_dump(mode="json"),
                ttl_seconds=self._settings.git_cache_ttl_seconds,
                source_token=source_token,
            )
            return response
        branch = _git(root, "branch", "--show-current", check=False).stdout.strip() or None
        diff = _git(root, "diff", "--", ".", check=False).stdout
        changed = [item.path for item in _parse_status(_git(root, "status", "--short", "--", ".", check=False).stdout)]
        response = GitDiffResponse(
            branch=branch,
            diff=_truncate(diff, limit),
            files_changed=changed,
        )
        self._cache.set(
            cache_key,
            response.model_dump(mode="json"),
            ttl_seconds=self._settings.git_cache_ttl_seconds,
            source_token=source_token,
        )
        return response

    def create_action_branch(self, action_id: str, summary: str) -> dict:
        root = get_workspace_root(self._settings.voiceops_workspace)
        if not _is_git_repo(root):
            return {
                "branch_created": False,
                "branch_name": None,
                "previous_branch": None,
                "warning": _git_repo_warning(root),
            }

        before = self.status(use_cache=False)
        branch_name = _branch_name(action_id, summary)
        warning = None
        branch_created = False
        try:
            existing = _git(root, "branch", "--list", branch_name, check=False).stdout.strip()
            if existing:
                result = _git(root, "switch", branch_name, check=False)
            else:
                result = _git(root, "switch", "-c", branch_name, check=False)
                branch_created = result.returncode == 0
            if result.returncode != 0:
                warning = _clean_git_error(result.stderr) or f"Could not switch to {branch_name}."
                branch_created = False
        except OSError as exc:
            warning = str(exc)
            branch_created = False

        self.invalidate()
        dirty_warning = "Workspace had uncommitted changes before approval." if before.dirty else None
        return {
            "branch_created": branch_created,
            "branch_name": branch_name,
            "previous_branch": before.branch,
            "dirty_before": before.dirty,
            "status_before": [item.model_dump() for item in before.files],
            "warning": warning or dirty_warning,
        }

    def commit_files(self, *, summary: str, files: list[str], message: str | None = None) -> dict:
        root = get_workspace_root(self._settings.voiceops_workspace)
        if not _is_git_repo(root):
            raise WorkspaceError(_git_repo_warning(root))
        scoped_files = _safe_paths(files)
        if not scoped_files:
            raise WorkspaceError("No changed files are available to commit.")

        before = self.status(use_cache=False)
        _git(root, "add", "--", *scoped_files)
        staged = _git(root, "diff", "--cached", "--quiet", "--", *scoped_files, check=False)
        if staged.returncode == 0:
            raise WorkspaceError("No staged changes for this action.")
        if staged.returncode not in (0, 1):
            raise WorkspaceError(_clean_git_error(staged.stderr) or "Could not inspect staged changes.")

        commit_message = _commit_message(message, summary)
        _git(root, "commit", "-m", commit_message, "--", *scoped_files)
        self.invalidate()
        sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
        after = self.status(use_cache=False)
        return {
            "commit_created": True,
            "commit_sha": sha,
            "commit_message": commit_message,
            "committed_files": scoped_files,
            "dirty_before_commit": before.dirty,
            "status_before_commit": [item.model_dump() for item in before.files],
            "dirty_after_commit": after.dirty,
            "status_after_commit": [item.model_dump() for item in after.files],
        }

    def invalidate(self) -> None:
        root = get_workspace_root(self._settings.voiceops_workspace)
        self._cache.delete_prefix(_cache_prefix(root))


def _is_git_repo(root: Path) -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _cache_prefix(root: Path) -> str:
    digest = sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"git:{digest}:"


def _cache_key(root: Path, name: str) -> str:
    return f"{_cache_prefix(root)}{name}"


def _workspace_source_token(root: Path) -> str:
    digest = sha1()
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
        if ".git" in path.relative_to(root).parts or not path.is_file():
            continue
        stat = path.stat()
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
    if _is_git_repo(root):
        head = _git(root, "rev-parse", "--verify", "HEAD", check=False).stdout.strip()
        branch = _git(root, "branch", "--show-current", check=False).stdout.strip()
        digest.update(head.encode("utf-8"))
        digest.update(branch.encode("utf-8"))
    return digest.hexdigest()


def _git_repo_warning(root: Path) -> str:
    top_level = _git(root, "rev-parse", "--show-toplevel", check=False)
    if top_level.returncode == 0 and top_level.stdout.strip():
        return "Workspace is inside a larger git repository; git status and diff are scoped to the configured workspace."
    return "Connected workspace is not a git repository."


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise WorkspaceError(_clean_git_error(result.stderr) or f"git {' '.join(args)} failed")
    return result


def _parse_status(output: str) -> list[GitFileStatus]:
    files: list[GitFileStatus] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status = line[:2].strip() or line[:2]
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(GitFileStatus(path=path, status=status))
    return files


def _branch_name(action_id: str, summary: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")[:32]
    slug = slug or "patch"
    safe_action = re.sub(r"[^a-zA-Z0-9-]+", "-", action_id).strip("-")
    return f"voiceops/{safe_action}-{slug}"


def _safe_paths(files: list[str]) -> list[str]:
    safe: list[str] = []
    for file in files:
        path = str(file).strip()
        if not path or path.startswith("/") or ".." in Path(path).parts:
            continue
        if path not in safe:
            safe.append(path)
    return safe


def _commit_message(message: str | None, summary: str) -> str:
    text = " ".join((message or "").split())
    if not text:
        text = f"VoiceOps: {' '.join(summary.split())[:72]}"
    return text[:200] or "VoiceOps: approved patch"


def _clean_git_error(value: str) -> str:
    return " ".join((value or "").strip().split())


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 80] + "\n... truncated ..."
