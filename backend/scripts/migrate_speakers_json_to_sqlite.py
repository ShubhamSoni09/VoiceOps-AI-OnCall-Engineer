from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.speakers.migration import migrate_json_to_sqlite


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Migrate speaker JSON store to SQLite.")
    parser.add_argument("--json", type=Path, default=settings.speaker_store_path, help="Source JSON store path")
    parser.add_argument("--sqlite", type=Path, default=settings.speaker_sqlite_path, help="Target SQLite path")
    parser.add_argument("--replace", action="store_true", help="Replace target SQLite file if it already exists")
    args = parser.parse_args()

    result = migrate_json_to_sqlite(args.json, args.sqlite, replace=args.replace)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
