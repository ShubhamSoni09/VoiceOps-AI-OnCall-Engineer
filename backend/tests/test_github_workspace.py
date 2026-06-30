import base64

from app.config import Settings
from app.collab.models import AgentAction
from app.workspace import github
from app.workspace.git import WorkspaceGitService
from app.workspace.service import WorkspaceCodeService


def test_github_workspace_reads_tree_file_search_and_status(monkeypatch, tmp_path):
    app_py = "from fastapi import FastAPI\napp = FastAPI()\n"

    def fake_api(_settings, method, path, payload=None):
        assert method == "GET"
        if path == "/repos/team/app":
            return {"name": "app", "default_branch": "trunk", "html_url": "https://github.com/team/app"}
        if path == "/repos/team/app/git/trees/trunk?recursive=1":
            return {"tree": [{"path": "app.py", "type": "blob", "size": len(app_py)}]}
        if path == "/repos/team/app/contents/app.py?ref=trunk":
            return {
                "type": "file",
                "size": len(app_py),
                "content": base64.b64encode(app_py.encode("utf-8")).decode("ascii"),
            }
        raise AssertionError(path)

    monkeypatch.setattr(github, "_github_api", fake_api)
    settings = Settings(voiceops_workspace="https://github.com/team/app.git", voiceops_cache_path=tmp_path / "cache.json")

    tree = WorkspaceCodeService(settings).tree()
    assert tree.mode == "github"
    assert [item.path for item in tree.files] == ["app.py"]

    read = WorkspaceCodeService(settings).read("app.py")
    assert read.content == app_py

    search = WorkspaceCodeService(settings).search("FastAPI")
    assert search.mode == "github"
    assert search.matches[0].path == "app.py"

    status = WorkspaceGitService(settings).status()
    assert status.is_git_repo is True
    assert status.branch == "trunk"


def test_create_github_patch_pull_request_uses_api_without_checkout(monkeypatch, tmp_path):
    calls = []

    def fake_api(_settings, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "/repos/team/app":
            return {"name": "app", "default_branch": "main", "html_url": "https://github.com/team/app"}
        if method == "GET" and path == "/repos/team/app/git/ref/heads/main":
            return {"object": {"sha": "base123"}}
        if method == "GET" and path.startswith("/repos/team/app/git/ref/heads/voiceops/"):
            raise github.WorkspaceError("GitHub API failed (404): Not Found")
        if method == "POST" and path == "/repos/team/app/git/refs":
            assert payload["sha"] == "base123"
            return {}
        if method == "GET" and path.startswith("/repos/team/app/contents/app.py?ref=voiceops/"):
            raise github.WorkspaceError("GitHub API failed (404): Not Found")
        if method == "PUT" and path == "/repos/team/app/contents/app.py":
            assert payload["branch"].startswith("voiceops/")
            return {"commit": {"sha": "commit123"}}
        if method == "POST" and path == "/repos/team/app/pulls":
            return {"html_url": "https://github.com/team/app/pull/7", "number": 7, "draft": True}
        raise AssertionError((method, path))

    monkeypatch.setattr(github, "_github_api", fake_api)
    settings = Settings(
        voiceops_workspace="https://github.com/team/app.git",
        github_pr_creation_enabled=True,
        voiceops_cache_path=tmp_path / "cache.json",
    )
    action = AgentAction(
        id="act-1",
        room_id="main",
        requested_by="u1",
        requested_by_name="Priya",
        action="patch",
        summary="Add health endpoint",
        created_at="2026-06-28T00:00:00Z",
    )

    result = github.create_github_patch_pull_request(settings, action, files={"app.py": "print('ok')\n"}, approved_by="u1")

    assert result["pull_request_url"] == "https://github.com/team/app/pull/7"
    assert result["commit_sha"] == "commit123"
    assert any(call[0] == "PUT" and call[1] == "/repos/team/app/contents/app.py" for call in calls)
