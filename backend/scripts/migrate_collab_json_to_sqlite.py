from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collab.migration import migrate_json_to_sqlite
from app.config import get_settings


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Migrate collaboration JSON store to SQLite.")
    parser.add_argument("--json", type=Path, default=settings.collab_store_path, help="Source JSON store path")
    parser.add_argument("--sqlite", type=Path, default=settings.collab_sqlite_path, help="Target SQLite path")
    parser.add_argument("--replace", action="store_true", help="Replace target SQLite file if it already exists")
    parser.add_argument("--dry-run", action="store_true", help="Inspect source counts without writing SQLite")
    parser.add_argument("--no-validate", action="store_true", help="Skip post-migration count validation")
    parser.add_argument("--report-json", action="store_true", help="Print a machine-readable JSON report")
    args = parser.parse_args(argv)

    try:
        result = migrate_json_to_sqlite(
            args.json,
            args.sqlite,
            replace=args.replace,
            dry_run=args.dry_run,
            validate=not args.no_validate,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
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
