from __future__ import annotations

from app.config import Settings
from app.integrations.github.store import GitHubConnection, get_github_token_store


def normalize_repo_slug(repo: str) -> str:
    return (
        repo.strip()
        .removeprefix("https://github.com/")
        .removesuffix(".git")
    )


def repo_dir_name(repo_slug: str) -> str:
    return repo_slug.replace("/", "__").replace("\\", "__")


def get_user_selected_repo(user_id: str | None, settings: Settings) -> str | None:
    if not user_id:
        return settings.github_repo
    conn = get_github_token_store().get(user_id)
    if conn and conn.selected_repo:
        return conn.selected_repo
    return settings.github_repo


def set_user_selected_repo(user_id: str, repo: str) -> GitHubConnection:
    store = get_github_token_store()
    conn = store.get(user_id)
    if conn is None:
        raise ValueError("GitHub is not connected for this user")
    slug = normalize_repo_slug(repo)
    updated = conn.model_copy(update={"selected_repo": slug})
    store.set(user_id, updated)
    return updated
