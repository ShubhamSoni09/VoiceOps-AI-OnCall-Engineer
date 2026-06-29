import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_WORKSPACE = REPO_ROOT / "sandbox"
WORKSPACE_URL_PATTERN = re.compile(r"^(?:https?://|git@|ssh://|git://)", re.IGNORECASE)

BLOCKED_COMMAND_PATTERNS = re.compile(
    r"(;|&&|\|\||`|\$\(|\brm\b|\bdel\b|\bformat\b|\bshutdown\b|\brestart\b|"
    r"\bremove-item\b|\berase\b|\bmkfs\b|\bdd\b)",
    re.IGNORECASE,
)

ALLOWED_COMMANDS: dict[str, set[str] | None] = {
    "python": None,
    "pytest": None,
    "pip": None,
    "git": {"status", "diff", "log", "branch", "show"},
}


class WorkspaceError(ValueError):
    pass


def configured_workspace_is_url(configured: str | None) -> bool:
    return bool(configured and WORKSPACE_URL_PATTERN.search(configured.strip()))


def resolve_configured_workspace(configured: str | None) -> Path | None:
    if not configured or not configured.strip():
        return None
    if configured_workspace_is_url(configured):
        return None
    root = Path(configured.strip())
    if not root.is_absolute():
        root = (REPO_ROOT / root).resolve()
    else:
        root = root.resolve()
    if not root.exists() or not root.is_dir():
        return None
    return root


def get_workspace_root(configured: str | None = None) -> Path:
    if configured is None:
        configured = os.environ.get("VOICEOPS_WORKSPACE", "").strip() or None
    root = resolve_configured_workspace(configured)
    if root is None:
        if DEFAULT_WORKSPACE.exists():
            return DEFAULT_WORKSPACE.resolve()
        raise WorkspaceError("No workspace configured. Set VOICEOPS_WORKSPACE in backend/.env")
    return root


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_workspace_path(relative_path: str, *, configured: str | None = None) -> Path:
    root = get_workspace_root(configured)
    candidate = (root / relative_path).resolve()
    if not _is_within_root(candidate, root):
        raise WorkspaceError(f"Path escapes workspace: {relative_path}")
    return candidate


def list_directory(relative_path: str = ".", *, configured: str | None = None) -> list[dict]:
    target = resolve_workspace_path(relative_path, configured=configured)
    if not target.exists():
        raise WorkspaceError(f"Path not found: {relative_path}")
    if not target.is_dir():
        raise WorkspaceError(f"Not a directory: {relative_path}")

    root = get_workspace_root(configured)
    entries = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        entries.append(
            {
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "path": str(entry.relative_to(root)).replace("\\", "/"),
            }
        )
    return entries


def read_file(relative_path: str, *, configured: str | None = None) -> str:
    target = resolve_workspace_path(relative_path, configured=configured)
    if not target.exists():
        raise WorkspaceError(f"File not found: {relative_path}")
    if not target.is_file():
        raise WorkspaceError(f"Not a file: {relative_path}")
    return target.read_text(encoding="utf-8")


def write_file(relative_path: str, content: str, *, configured: str | None = None) -> dict:
    target = resolve_workspace_path(relative_path, configured=configured)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": relative_path,
        "bytes_written": len(content.encode("utf-8")),
        "status": "written",
    }


def validate_command(command: str) -> None:
    command = command.strip()
    if not command:
        raise WorkspaceError("Command cannot be empty")
    if BLOCKED_COMMAND_PATTERNS.search(command):
        raise WorkspaceError("Command contains blocked operators or keywords")

    parts = command.split()
    executable = Path(parts[0]).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]

    allowed_subcommands = ALLOWED_COMMANDS.get(executable)
    if allowed_subcommands is None and executable not in ALLOWED_COMMANDS:
        raise WorkspaceError(
            f"Command not allowed: {parts[0]}. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )

    if allowed_subcommands is not None and len(parts) > 1:
        subcommand = parts[1].lower()
        if subcommand not in allowed_subcommands:
            raise WorkspaceError(
                f"git subcommand not allowed: {subcommand}. "
                f"Allowed: {', '.join(sorted(allowed_subcommands))}"
            )


def run_command(command: str, *, configured: str | None = None, timeout: int = 120) -> dict:
    validate_command(command)
    root = get_workspace_root(configured)
    result = subprocess.run(
        command,
        shell=True,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
