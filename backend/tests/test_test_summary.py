from app.orchestrator.test_summary import build_investigation_summary, summarize_pytest

SAMPLE_FAILURE = """
.F                                                                       [100%]
================================== FAILURES ===================================
_________________________________ test_health _________________________________

    def test_health() -> None:
        response = client.get("/health")
>       assert response.status_code == 200
E       assert 404 == 200

tests/test_app.py:16: AssertionError
"""


def test_summarize_health_failure():
    info = summarize_pytest(SAMPLE_FAILURE, "", app_source='return {"service": "checkout-api"}')
    assert info.passed is False
    assert info.failed_count == 1
    assert "test_health" in info.failing_tests
    assert info.suggestion is not None
    assert "health" in info.suggestion


def test_investigation_summary_is_readable():
    app_source = 'return {"service": "checkout-api", "status": "ok"}'
    summary = build_investigation_summary(
        "sandbox",
        ["app.py", "README.md"],
        app_source,
        {"stdout": SAMPLE_FAILURE, "stderr": "", "exit_code": 1},
    )
    assert "checkout-api" in summary
    assert "404" in summary or "health" in summary.lower()
    assert "pytest failed:" not in summary.lower()
    assert "AssertionError" not in summary
