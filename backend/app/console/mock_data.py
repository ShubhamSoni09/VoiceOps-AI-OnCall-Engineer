"""Mock incidents and artifacts for sandbox UI + voice testing."""

from __future__ import annotations

from typing import Any


def _has_health(source: str) -> bool:
    return '@app.get("/health")' in source or "def health(" in source


def _has_charge(source: str) -> bool:
    return '"/v2/charge"' in source or "'/v2/charge'" in source


def _has_metrics_schema(source: str) -> bool:
    return '"error_rate"' in source and "error_rate_pct" not in source


def _agent_status(bug_key: str, source: str, all_tests_pass: bool) -> str:
    if all_tests_pass:
        return "verified"
    fixed = {
        "health": _has_health(source),
        "charge": _has_charge(source),
        "metrics": _has_metrics_schema(source),
    }
    if fixed.get(bug_key):
        return "patched · waiting on remaining tests"
    ready = {"health": "investigate", "charge": "patch", "metrics": "diagnose"}
    return f"ready · {ready.get(bug_key, 'patch')}"


def build_sandbox_mock_incidents(
    source: str,
    *,
    all_tests_pass: bool,
    failing_tests: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Incidents with bug_key stay active until pytest is fully green."""
    incidents: list[dict[str, Any]] = [
        {
            "id": "INC-2026-0612-01",
            "title": "checkout-api returning 500s on /v2/charge",
            "service": "checkout-api",
            "severity": "SEV1",
            "status": "active",
            "error_rate": "4.7%",
            "reporter": "PagerDuty P1",
            "agent_status": _agent_status("charge", source, all_tests_pass),
            "age_minutes": 6,
            "voice_hint": "Fix the charge endpoint in sandbox",
            "bug_key": "charge",
        },
        {
            "id": "INC-2026-0612-02",
            "title": "Health check endpoint missing on checkout-api",
            "service": "checkout-api",
            "severity": "SEV2",
            "status": "active",
            "error_rate": "0.4%",
            "reporter": "Synthetic monitor",
            "agent_status": _agent_status("health", source, all_tests_pass),
            "age_minutes": 18,
            "voice_hint": "Fix the health endpoint",
            "bug_key": "health",
        },
        {
            "id": "INC-2026-0612-03",
            "title": "Metrics schema mismatch — error_rate field missing",
            "service": "checkout-api",
            "severity": "SEV2",
            "status": "active",
            "error_rate": "4.7%",
            "reporter": "Grafana alert",
            "agent_status": _agent_status("metrics", source, all_tests_pass),
            "age_minutes": 42,
            "voice_hint": "Fix the metrics schema in sandbox",
            "bug_key": "metrics",
        },
        {
            "id": "INC-2026-0612-04",
            "title": "Retry storm, payments worker",
            "service": "payments-worker",
            "severity": "SEV2",
            "status": "resolved",
            "error_rate": "0.02%",
            "reporter": "Slack #incidents",
            "agent_status": "verified",
            "age_minutes": 128,
            "voice_hint": None,
        },
    ]

    for inc in incidents:
        if inc.get("bug_key"):
            inc["status"] = "resolved" if all_tests_pass else "active"

    open_incidents = [i for i in incidents if i["status"] != "resolved"]
    critical = len([i for i in open_incidents if str(i.get("severity", "")).upper().startswith("SEV1")])
    active = len(open_incidents)
    return incidents, {"critical": critical, "active": active}


def build_sandbox_mock_metrics(
    *,
    all_tests_pass: bool,
    failing_tests: int = 0,
) -> list[dict[str, Any]]:
    failing = 0 if all_tests_pass else max(failing_tests, 1)
    return [
        {
            "label": "Error rate",
            "value": "0.06" if all_tests_pass else "4.7",
            "unit": "%",
            "status": "ok" if all_tests_pass else "bad",
            "delta": "stable" if all_tests_pass else "up from 0.06%",
            "trend": None if all_tests_pass else "up",
        },
        {
            "label": "p99 latency",
            "value": "210" if all_tests_pass else "1,840",
            "unit": "ms",
            "status": "ok" if all_tests_pass else "warn",
            "delta": "normal" if all_tests_pass else "6.1× baseline",
            "trend": "down" if all_tests_pass else "up",
        },
        {
            "label": "Failing tests",
            "value": "0" if all_tests_pass else str(failing),
            "unit": "of 4",
            "status": "ok" if all_tests_pass else "bad",
            "delta": "pytest sandbox",
            "trend": None if all_tests_pass else "up",
        },
        {
            "label": "Error budget",
            "value": "99.1" if all_tests_pass else "87.3",
            "unit": "%",
            "status": "ok",
            "delta": "healthy" if all_tests_pass else "down 9.4% today",
            "trend": None if all_tests_pass else "down",
        },
    ]


def build_sandbox_mock_artifacts(
    *,
    all_tests_pass: bool,
    failing_tests: int = 0,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = [
        {
            "type": "runbook",
            "title": "Runbook · sandbox test guide",
            "subtitle": "sandbox/README.md",
        },
    ]
    if all_tests_pass:
        artifacts.insert(
            0,
            {
                "type": "pr",
                "title": "All sandbox tests passing",
                "subtitle": "pytest green · incidents resolved",
            },
        )
    else:
        artifacts.insert(
            0,
            {
                "type": "logs",
                "title": "pytest · checkout-api",
                "subtitle": f"{max(failing_tests, 1)} failing · fix all before resolve",
            },
        )
        artifacts.insert(
            1,
            {
                "type": "logs",
                "title": "app.py · workspace snapshot",
                "subtitle": "incidents stay open until all tests pass",
            },
        )
    return artifacts
