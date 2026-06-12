import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = REPO_ROOT / "sandbox"

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


def get_workspace_root() -> Path:
    configured = os.environ.get("VOICEOPS_WORKSPACE", "").strip()
    root = Path(configured) if configured else DEFAULT_WORKSPACE
    if not root.is_absolute():
        root = (REPO_ROOT / root).resolve()
    else:
        root = root.resolve()

    if not root.exists():
        raise WorkspaceError(f"Workspace does not exist: {root}")
    if not root.is_dir():
        raise WorkspaceError(f"Workspace is not a directory: {root}")
    return root


def resolve_workspace_path(relative_path: str) -> Path:
    root = get_workspace_root()
    candidate = (root / relative_path).resolve()
    if not _is_within_root(candidate, root):
        raise WorkspaceError(f"Path escapes workspace: {relative_path}")
    return candidate


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
            f"Command not allowed: {parts[0]}. "
            f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )

    if allowed_subcommands is not None and len(parts) > 1:
        subcommand = parts[1].lower()
        if subcommand not in allowed_subcommands:
            raise WorkspaceError(
                f"git subcommand not allowed: {subcommand}. "
                f"Allowed: {', '.join(sorted(allowed_subcommands))}"
            )


def run_workspace_command(command: str, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    validate_command(command)
    root = get_workspace_root()
    return subprocess.run(
        command,
        shell=True,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
