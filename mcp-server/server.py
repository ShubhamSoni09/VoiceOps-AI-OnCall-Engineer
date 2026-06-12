"""VoiceOps workspace MCP server — file and shell tools scoped to sandbox/."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from workspace import (
    WorkspaceError,
    get_workspace_root,
    resolve_workspace_path,
    run_workspace_command,
)

mcp = FastMCP(
    "voiceops-workspace",
    instructions=(
        "Tools for reading, editing, searching, and testing code in the VoiceOps sandbox workspace. "
        "Use relative paths from the workspace root. Prefer running pytest after code changes."
    ),
)


def _json_result(payload: dict) -> str:
    return json.dumps(payload, indent=2)


@mcp.tool()
def workspace_info() -> str:
    """Return the active workspace path and a brief description."""
    root = get_workspace_root()
    readme = root / "README.md"
    description = ""
    if readme.exists():
        description = readme.read_text(encoding="utf-8").splitlines()[0].strip()

    return _json_result(
        {
            "workspace": str(root),
            "description": description or "VoiceOps sandbox project",
        }
    )


@mcp.tool()
def list_directory(relative_path: str = ".") -> str:
    """List files and folders under a workspace-relative path."""
    target = resolve_workspace_path(relative_path)
    if not target.exists():
        raise WorkspaceError(f"Path not found: {relative_path}")
    if not target.is_dir():
        raise WorkspaceError(f"Not a directory: {relative_path}")

    entries = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        entries.append(
            {
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "path": str(entry.relative_to(get_workspace_root())).replace("\\", "/"),
            }
        )
    return _json_result({"path": relative_path, "entries": entries})


@mcp.tool()
def read_file(relative_path: str) -> str:
    """Read a text file from the workspace."""
    target = resolve_workspace_path(relative_path)
    if not target.exists():
        raise WorkspaceError(f"File not found: {relative_path}")
    if not target.is_file():
        raise WorkspaceError(f"Not a file: {relative_path}")

    content = target.read_text(encoding="utf-8")
    return _json_result({"path": relative_path, "content": content})


@mcp.tool()
def write_file(relative_path: str, content: str) -> str:
    """Create or overwrite a text file in the workspace."""
    target = resolve_workspace_path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return _json_result(
        {
            "path": relative_path,
            "bytes_written": len(content.encode("utf-8")),
            "status": "written",
        }
    )


@mcp.tool()
def search_files(query: str, relative_path: str = ".") -> str:
    """Search for text in files under a workspace-relative directory."""
    root = resolve_workspace_path(relative_path)
    if not root.exists():
        raise WorkspaceError(f"Path not found: {relative_path}")

    matches: list[dict] = []
    workspace_root = get_workspace_root()
    skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}

    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    for file_path in files:
        if any(part in skip_dirs for part in file_path.parts):
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                matches.append(
                    {
                        "path": str(file_path.relative_to(workspace_root)).replace("\\", "/"),
                        "line": line_no,
                        "text": line.strip(),
                    }
                )
                if len(matches) >= 50:
                    break
        if len(matches) >= 50:
            break

    return _json_result({"query": query, "match_count": len(matches), "matches": matches})


@mcp.tool()
def run_command(command: str) -> str:
    """Run an allowlisted shell command in the workspace (python, pytest, pip, git)."""
    result = run_workspace_command(command)
    return _json_result(
        {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )


if __name__ == "__main__":
    mcp.run()
