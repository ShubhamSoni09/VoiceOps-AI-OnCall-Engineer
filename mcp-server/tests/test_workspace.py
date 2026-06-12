import pytest

from workspace import WorkspaceError, resolve_workspace_path, validate_command


def test_resolve_blocks_path_traversal():
    with pytest.raises(WorkspaceError, match="escapes workspace"):
        resolve_workspace_path("../backend/app/main.py")


def test_validate_blocks_dangerous_commands():
    with pytest.raises(WorkspaceError):
        validate_command("rm -rf .")
    with pytest.raises(WorkspaceError):
        validate_command("python -c 'print(1)' && del file")


def test_validate_allows_pytest():
    validate_command("pytest")
