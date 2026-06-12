from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

import httpx

GITHUB_API = "https://api.github.com"


class GitHubApiError(RuntimeError):
    pass


def parse_github_repo(repo: str) -> tuple[str, str]:
    cleaned = repo.strip().removeprefix("https://github.com/").removesuffix(".git")
    if "/" not in cleaned:
        raise GitHubApiError(f"Invalid GITHUB_REPO: {repo}")
    owner, name = cleaned.split("/", 1)
    return owner, name


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "fix"


async def list_accessible_repos(access_token: str, *, limit: int = 50) -> list[dict]:
    """Return repos the user can read (owner, collaborator, org member)."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{GITHUB_API}/user/repos",
            headers=_headers(access_token),
            params={
                "sort": "updated",
                "per_page": min(limit, 100),
                "affiliation": "owner,collaborator,organization_member",
            },
        )
    if response.status_code >= 400:
        raise GitHubApiError(f"Could not list repositories: {response.text}")

    repos: list[dict] = []
    for item in response.json():
        full_name = item.get("full_name")
        if not full_name:
            continue
        repos.append(
            {
                "full_name": full_name,
                "name": item.get("name"),
                "private": bool(item.get("private")),
                "default_branch": item.get("default_branch") or "main",
                "description": item.get("description") or "",
                "updated_at": item.get("updated_at"),
            }
        )
    return repos


async def create_fix_pull_request(
    access_token: str,
    owner: str,
    repo: str,
    *,
    files: dict[str, str],
    title: str,
    body: str,
    branch_prefix: str = "voiceops",
) -> dict:
    """Create a branch, commit file updates, and open a pull request."""
    if not files:
        raise GitHubApiError("No files to commit")

    async with httpx.AsyncClient(timeout=60) as client:
        repo_resp = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=_headers(access_token))
        if repo_resp.status_code >= 400:
            raise GitHubApiError(f"Cannot access {owner}/{repo}: {repo_resp.text}")
        repo_data = repo_resp.json()
        base_branch = repo_data.get("default_branch") or "main"

        ref_resp = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
            headers=_headers(access_token),
        )
        if ref_resp.status_code >= 400:
            raise GitHubApiError(f"Cannot read base branch {base_branch}")
        base_sha = ref_resp.json()["object"]["sha"]

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        branch_name = f"{branch_prefix}/{_slug(title)}-{stamp}"

        create_ref = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
            headers=_headers(access_token),
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        if create_ref.status_code >= 400:
            raise GitHubApiError(f"Could not create branch {branch_name}: {create_ref.text}")

        for path, content in files.items():
            file_path = path.lstrip("/")
            sha = None
            existing = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/contents/{file_path}",
                headers=_headers(access_token),
                params={"ref": base_branch},
            )
            if existing.status_code == 200:
                sha = existing.json().get("sha")

            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            put_resp = await client.put(
                f"{GITHUB_API}/repos/{owner}/{repo}/contents/{file_path}",
                headers=_headers(access_token),
                json={
                    "message": title,
                    "content": encoded,
                    "branch": branch_name,
                    **({"sha": sha} if sha else {}),
                },
            )
            if put_resp.status_code >= 400:
                raise GitHubApiError(f"Could not update {file_path}: {put_resp.text}")

        pr_resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=_headers(access_token),
            json={
                "title": title,
                "body": body,
                "head": branch_name,
                "base": base_branch,
            },
        )
        if pr_resp.status_code >= 400:
            raise GitHubApiError(f"Could not open pull request: {pr_resp.text}")

        pr = pr_resp.json()
        return {
            "number": pr.get("number"),
            "url": pr.get("html_url"),
            "branch": branch_name,
            "base": base_branch,
        }
