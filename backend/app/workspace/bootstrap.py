from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import Settings
from app.integrations.github.user_repo import normalize_repo_slug, repo_dir_name


def user_workspace_path(user_id: str, settings: Settings, repo: str) -> Path:
    slug = normalize_repo_slug(repo)
    return settings.data_dir / "workspaces" / user_id / repo_dir_name(slug)


def ensure_workspace(settings: Settings) -> None:
    """Clone or refresh the GitHub repo using the server deploy token."""
    if not settings.github_repo or not settings.github_deploy_token:
        return

    target = (
        Path(settings.voiceops_workspace)
        if settings.voiceops_workspace
        else settings.data_dir / "workspace"
    )
    _clone_or_pull(target, normalize_repo_slug(settings.github_repo), settings.github_deploy_token)


def ensure_user_workspace(user_id: str, access_token: str, settings: Settings, repo: str) -> Path:
    """Clone or refresh the selected repo into the user's workspace."""
    slug = normalize_repo_slug(repo)
    target = user_workspace_path(user_id, settings, slug)
    _clone_or_pull(target, slug, access_token)
    return target


def _clone_or_pull(target: Path, repo_slug: str, token: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://{token}@github.com/{repo_slug}.git"

    if (target / ".git").exists():
        _run_git(target, "pull", "--ff-only")
        return

    if target.exists():
        for child in target.iterdir():
            if child.is_dir():
                import shutil
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    result = subprocess.run(
        ["git", "clone", "--depth", "1", clone_url, str(target)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0 and not (target / ".git").exists():
        raise RuntimeError(f"Workspace clone failed: {result.stderr or result.stdout}")


def _run_git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
