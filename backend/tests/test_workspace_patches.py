from app.orchestrator.workspace_patches import (
    apply_best_patch,
    apply_targeted_patch,
    detect_patch_target,
)

BROKEN = '''"""sandbox"""
from fastapi import FastAPI
app = FastAPI(title="checkout-api")

@app.get("/")
def root():
    return {"service": "checkout-api", "status": "ok"}

@app.get("/metrics")
def metrics():
    return {"error_rate_pct": 4.7}
'''


def test_detect_charge_from_transcript():
    assert detect_patch_target("Fix the charge endpoint in sandbox", "") == "charge"


def test_detect_health_from_transcript():
    assert detect_patch_target("fix the health endpoint", "") == "health"


def test_patch_charge_adds_route():
    updated, desc = apply_targeted_patch(BROKEN, "charge")
    assert "/v2/charge" in updated
    assert "charge" in desc.lower()


def test_patch_health_adds_route():
    updated, desc = apply_targeted_patch(BROKEN, "health")
    assert '@app.get("/health")' in updated
    assert "health" in desc.lower()


def test_patch_metrics_fixes_schema():
    updated, desc = apply_targeted_patch(BROKEN, "metrics")
    assert "error_rate_pct" not in updated or '"error_rate"' in updated
    assert "error_rate" in desc.lower()


def test_best_patch_respects_transcript_over_health():
    updated, desc = apply_best_patch(BROKEN, "Fix the charge endpoint", "test_health FAILED")
    assert "/v2/charge" in updated
    assert "charge" in desc.lower()
