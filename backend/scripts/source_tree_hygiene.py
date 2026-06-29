from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


REQUIRED_IGNORED_PATHS = (
    "backend/.env",
    "backend/.voiceops_cache/cache.json",
    "backend/.voiceops_memory.json",
    "backend/data/users.json",
    "backend/data/collaboration.sqlite3",
    "backend/data/speakers.sqlite3",
    "backend/data/demo_evidence.json",
    "frontend/dist/index.html",
    "frontend/node_modules/.package-lock.json",
    "frontend/coverage/coverage-final.json",
    "frontend/playwright-report/index.html",
    "frontend/test-results/results.json",
    ".coverage",
    ".impeccable/live/config.json",
    "voiceops-production-trial/.env",
)

GENERATED_PATH_MARKERS = (
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "backend/.voiceops_cache/",
    "backend/data/",
    "frontend/dist/",
    "frontend/node_modules/",
    "frontend/coverage/",
    "frontend/playwright-report/",
    "frontend/test-results/",
    ".impeccable/",
    "voiceops-production-trial/",
    "production-trial/",
)

GENERATED_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".wav",
    ".mp3",
    ".lcov",
)


@dataclass
class HygieneItem:
    id: str
    label: str
    status: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def run_source_tree_hygiene(project_root: Path | None = None) -> dict:
    root = (project_root or PROJECT_ROOT).resolve()
    items = [
        _required_ignored_paths_item(root),
        _visible_generated_artifacts_item(root),
    ]
    failed = [item for item in items if not item.passed]
    return {
        "status": "passed" if not failed else "failed",
        "ready": not failed,
        "project_root": str(root),
        "items": [asdict(item) for item in items],
        "next_steps": _next_steps(failed),
    }


def _required_ignored_paths_item(root: Path) -> HygieneItem:
    missing: list[str] = []
    ignored: list[str] = []
    for path in REQUIRED_IGNORED_PATHS:
        if _is_ignored(root, path):
            ignored.append(path)
        else:
            missing.append(path)
    return HygieneItem(
        id="required_ignore_rules",
        label="Runtime, secret, build, and tool artifacts are ignored",
        status="passed" if not missing else "failed",
        evidence=[f"ignored={len(ignored)}"],
        gaps=[f"Add .gitignore coverage for {path}" for path in missing],
    )


def _visible_generated_artifacts_item(root: Path) -> HygieneItem:
    visible = [
        path for path in _untracked_visible_files(root)
        if _looks_generated_or_runtime(path)
    ]
    return HygieneItem(
        id="visible_generated_artifacts",
        label="No generated/runtime artifacts are visible to git",
        status="passed" if not visible else "failed",
        evidence=[f"visible_untracked={len(visible)}"],
        gaps=[f"Generated/runtime artifact is visible to git: {path}" for path in visible[:30]],
    )


def _is_ignored(root: Path, path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", path],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _untracked_visible_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _looks_generated_or_runtime(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.endswith(GENERATED_SUFFIXES):
        return True
    return any(marker in normalized or normalized.startswith(marker) for marker in GENERATED_PATH_MARKERS)


def _next_steps(failed: list[HygieneItem]) -> list[str]:
    steps: list[str] = []
    for item in failed:
        steps.extend(item.gaps)
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that VoiceOps runtime, secret, build, and local tool artifacts stay out of source control.",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args(argv)

    report = run_source_tree_hygiene(args.project_root)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text_report(report)
    if args.require_clean and not report["ready"]:
        return 1
    return 0


def _print_text_report(report: dict) -> None:
    print(f"Source tree hygiene: {report['status']}")
    for item in report["items"]:
        print(f"- {item['label']}: {item['status']}")
        for gap in item["gaps"]:
            print(f"  - {gap}")


if __name__ == "__main__":
    raise SystemExit(main())
