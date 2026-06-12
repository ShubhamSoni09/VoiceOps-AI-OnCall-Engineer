"""Turn raw pytest output into short, human-readable summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PytestSummary:
    passed: bool
    total: int = 0
    failed_count: int = 0
    passed_count: int = 0
    failing_tests: list[str] = field(default_factory=list)
    failure_lines: list[str] = field(default_factory=list)
    suggestion: str | None = None


def app_has_health_route(source: str | None) -> bool:
    if not source:
        return False
    return '@app.get("/health")' in source or "def health(" in source


def summarize_pytest(stdout: str, stderr: str, *, app_source: str | None = None) -> PytestSummary:
    combined = f"{stdout}\n{stderr}".strip()
    dots = re.search(r"^([\.FEx\s]+)\s*\[\s*\d+%\]", combined, re.MULTILINE)
    total = len(dots.group(1).replace(" ", "")) if dots else 0
    failed_count = dots.group(1).count("F") if dots else 0
    if total == 0:
        failed_count = len(re.findall(r"^FAILED\s+", combined, re.MULTILINE))
        total = max(failed_count, 1)

    failing_tests = re.findall(r"^FAILED\s+(\S+)", combined, re.MULTILINE)
    if not failing_tests:
        failing_tests = re.findall(r"_{3,}\s+(test_\w+)\s+_{3,}", combined)

    failure_lines: list[str] = []
    for test_name in failing_tests[:3]:
        block = _extract_failure_block(combined, test_name)
        line = _condense_failure(block)
        if line:
            failure_lines.append(f"{test_name}: {line}")

    passed = failed_count == 0 and "FAILED" not in combined
    suggestion = _suggest_fix(combined, failing_tests, app_source)

    return PytestSummary(
        passed=passed,
        total=total,
        failed_count=failed_count,
        passed_count=max(total - failed_count, 0),
        failing_tests=failing_tests,
        failure_lines=failure_lines,
        suggestion=suggestion,
    )


def build_investigation_summary(
    workspace_name: str,
    file_names: list[str],
    app_source: str | None,
    test_result: dict,
) -> str:
    pytest_info = summarize_pytest(
        test_result.get("stdout", ""),
        test_result.get("stderr", ""),
        app_source=app_source,
    )
    service = _detect_service_name(app_source) or workspace_name

    parts = [f"I investigated **{service}** in the {workspace_name} workspace."]

    if file_names:
        parts.append(f"Key files: {', '.join(file_names[:5])}.")

    if app_source and not app_has_health_route(app_source):
        parts.append("GET /health is not implemented yet — requests return 404.")

    if pytest_info.passed:
        parts.append(f"All {pytest_info.total} tests pass.")
    else:
        parts.append(
            f"{pytest_info.failed_count} of {pytest_info.total} tests failing"
            + (f" ({', '.join(pytest_info.failing_tests[:2])})." if pytest_info.failing_tests else ".")
        )
        if pytest_info.failure_lines:
            parts.append(pytest_info.failure_lines[0] + ".")
        if pytest_info.suggestion:
            parts.append(f"Recommended next step: {pytest_info.suggestion}")

    return " ".join(parts)


def build_test_summary(workspace_name: str, test_result: dict, *, app_source: str | None = None) -> str:
    pytest_info = summarize_pytest(
        test_result.get("stdout", ""),
        test_result.get("stderr", ""),
        app_source=app_source,
    )

    if pytest_info.passed:
        return f"All {pytest_info.total} tests passed in {workspace_name}."

    lines = [f"Tests failed in {workspace_name} — {pytest_info.failed_count} of {pytest_info.total} failing."]
    if pytest_info.failure_lines:
        lines.append(pytest_info.failure_lines[0] + ".")
    if pytest_info.suggestion:
        lines.append(f"Try: {pytest_info.suggestion}")
    return " ".join(lines)


def build_patch_summary(passed: bool, *, change: str = "Updated app.py.") -> str:
    if passed:
        return f"{change} All tests now pass."
    return f"{change} Some tests are still failing — investigate again or try the next fix."


def _detect_service_name(app_source: str | None) -> str | None:
    if not app_source:
        return None
    match = re.search(r'"service":\s*"([^"]+)"', app_source)
    return match.group(1) if match else None


def _extract_failure_block(text: str, test_name: str) -> str:
    pattern = rf"_{3,}\s+{re.escape(test_name)}\s+_{3,}(.*?)(?=_{3,}\s+test_|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else text


def _condense_failure(block: str) -> str:
    if 'client.get("/health")' in block or "/health" in block:
        status = re.search(r"assert (\d+) == (\d+)", block)
        if status and status.group(1) == "404":
            return "GET /health returns 404, expected 200"
    endpoint = re.search(r'client\.get\("([^"]+)"\)', block)
    status = re.search(r"assert (\d+) == (\d+)", block)
    if endpoint and status:
        return f'GET {endpoint.group(1)} returned {status.group(1)}, expected {status.group(2)}'
    line = re.search(r"^E\s+(.+)$", block, re.MULTILINE)
    return line.group(1).strip() if line else "assertion failed"


def _suggest_fix(text: str, failing_tests: list[str], app_source: str | None) -> str | None:
    if any("health" in name for name in failing_tests) or (
        app_source and not app_has_health_route(app_source)
    ):
        return 'add GET /health returning {"status": "ok"} to app.py'
    if "charge" in text.lower():
        return "fix the /v2/charge handler so it returns 200 for valid requests"
    if failing_tests:
        return f"fix {failing_tests[0].replace('test_', '').replace('_', ' ')}"
    return None
