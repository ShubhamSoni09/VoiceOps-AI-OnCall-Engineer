from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.collab.event_store_readiness import build_event_store_readiness
from app.collab.sqlite_store import SQLiteCollaborationStore
from app.config import Settings, get_settings


def build_parser(default_sqlite_path: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize the SQLite collaboration event store schema without writing demo events.",
    )
    parser.add_argument("--sqlite", type=Path, default=default_sqlite_path, help="Target SQLite event-store path.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing SQLite file before initializing.")
    parser.add_argument("--report-json", action="store_true", help="Print a machine-readable JSON report.")
    return parser


def init_event_store(sqlite_path: Path, *, replace: bool = False) -> dict:
    path = sqlite_path.expanduser()
    existed_before = path.exists()
    if existed_before and not replace:
        raise FileExistsError(f"SQLite event store already exists: {path}")
    if existed_before and replace:
        path.unlink()
    SQLiteCollaborationStore(path)
    readiness = build_event_store_readiness(
        Settings(collab_store_backend="sqlite", collab_sqlite_path=path)
    )
    return {
        "path": str(path),
        "created": not existed_before or replace,
        "replaced": existed_before and replace,
        "ready": readiness.ready,
        "event_count": readiness.event_count,
        "stream_count": readiness.stream_count,
        "checks": [check.model_dump(mode="json") for check in readiness.checks],
        "warnings": readiness.warnings,
    }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    args = build_parser(settings.collab_sqlite_path).parse_args(argv)
    try:
        result = init_event_store(args.sqlite, replace=args.replace)
    except (FileExistsError, OSError) as exc:
        if args.report_json:
            print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.report_json:
        print(json.dumps({"status": "ok", **result}, indent=2))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
