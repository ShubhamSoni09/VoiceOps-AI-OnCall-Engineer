from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = BACKEND_ROOT / "tests"

for path in (BACKEND_ROOT, TESTS_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from external_coding_agent_harness import main


if __name__ == "__main__":
    raise SystemExit(main())
