"""Targeted patches for sandbox app.py — one fix per voice command."""

from __future__ import annotations

import re


def has_health_route(source: str) -> bool:
    return '@app.get("/health")' in source or "def health(" in source


def has_charge_route(source: str) -> bool:
    return '"/v2/charge"' in source or "'/v2/charge'" in source


def has_metrics_schema(source: str) -> bool:
    return '"error_rate"' in source and "error_rate_pct" not in source


def detect_patch_target(transcript: str, failure: str) -> str | None:
    lower = transcript.lower()
    fail = failure.lower()

    if any(w in lower for w in ("charge", "500", "/v2", "v2/charge")):
        return "charge"
    if any(w in lower for w in ("metric", "schema", "error rate", "error_rate")):
        return "metrics"
    if any(w in lower for w in ("health", "healthcheck", "health check", "healthz")):
        return "health"

    if "test_charge" in fail or "/v2/charge" in fail:
        return "charge"
    if "test_metrics" in fail or "error_rate" in fail:
        return "metrics"
    if "test_health" in fail or 'get("/health")' in fail:
        return "health"
    return None


def apply_targeted_patch(source: str, target: str) -> tuple[str, str]:
    if target == "health":
        if has_health_route(source):
            return source, "GET /health already exists in app.py"
        updated = _add_health_route(source)
        return updated, "Added GET /health to app.py"

    if target == "charge":
        if has_charge_route(source):
            return source, "GET /v2/charge already exists in app.py"
        updated = source.rstrip() + (
            "\n\n@app.get(\"/v2/charge\")\n"
            "def charge(amount: float = 10.0) -> dict[str, str | float]:\n"
            '    return {"status": "charged", "amount": amount}\n'
        )
        return updated, "Added GET /v2/charge to app.py"

    if target == "metrics":
        if has_metrics_schema(source):
            return source, "Metrics schema already uses error_rate in app.py"
        updated = source
        if "error_rate_pct" in updated:
            updated = updated.replace("error_rate_pct", "error_rate")
        elif "@app.get(\"/metrics\")" in updated or "@app.get('/metrics')" in updated:
            # replace metrics body if wrong keys only
            updated = re.sub(
                r'(@app\.get\("/metrics"\)\s*\ndef metrics\(\)[^:]*:\s*\n\s*return\s*)\{[^}]+\}',
                r'\1{"error_rate": 0.06, "p99_latency_ms": 1840.0}',
                updated,
                count=1,
            )
        else:
            updated = updated.rstrip() + (
                '\n\n@app.get("/metrics")\n'
                "def metrics() -> dict[str, float]:\n"
                '    return {"error_rate": 0.06, "p99_latency_ms": 1840.0}\n'
            )
        return updated, "Fixed /metrics to expose error_rate in app.py"

    return source, "No patch applied"


def apply_best_patch(source: str, transcript: str, failure: str) -> tuple[str, str]:
    target = detect_patch_target(transcript, failure)
    if target:
        return apply_targeted_patch(source, target)

    # Fall back to first failing test in pytest output
    for name, key in (
        ("test_health", "health"),
        ("test_charge", "charge"),
        ("test_metrics", "metrics"),
    ):
        if name in failure:
            return apply_targeted_patch(source, key)

    if not has_health_route(source):
        return apply_targeted_patch(source, "health")
    if not has_charge_route(source):
        return apply_targeted_patch(source, "charge")
    if not has_metrics_schema(source):
        return apply_targeted_patch(source, "metrics")

    return source, "No changes needed in app.py"


def _add_health_route(source: str) -> str:
    insert = (
        '\n\n@app.get("/health")\n'
        "def health() -> dict[str, str]:\n"
        '    return {"status": "ok"}\n'
    )
    for marker in (
        "# GET /health is intentionally missing",
        "# Missing GET /health",
        "# Intentionally missing",
    ):
        if marker in source:
            return source.split(marker)[0].rstrip() + insert
    return source.rstrip() + insert
