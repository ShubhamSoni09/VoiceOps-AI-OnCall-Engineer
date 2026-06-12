from __future__ import annotations

from pathlib import Path


def resolve_ops_workspace(workspace: Path | str | None) -> Path | None:
    """Pick the directory for pytest/patches — e.g. repo root vs nested sandbox/."""
    if workspace is None:
        return None
    root = Path(workspace)
    if not root.is_dir():
        return None
    sandbox_app = root / "sandbox" / "app.py"
    if sandbox_app.is_file():
        return sandbox_app.parent.resolve()
    if (root / "app.py").is_file():
        return root.resolve()
    return root.resolve()
