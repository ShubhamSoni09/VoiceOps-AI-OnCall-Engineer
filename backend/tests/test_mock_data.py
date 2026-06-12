from app.console.mock_data import build_sandbox_mock_incidents


def test_incidents_stay_active_until_all_tests_pass():
    broken = "# Missing GET /health\nerror_rate_pct = 1\n"
    incidents, counts = build_sandbox_mock_incidents(broken, all_tests_pass=False, failing_tests=3)
    assert counts["active"] == 3
    checkout = [i for i in incidents if i.get("bug_key")]
    assert all(i["status"] == "active" for i in checkout)

    health_only = broken + '\n@app.get("/health")\ndef health(): return {"status": "ok"}\n'
    incidents, counts = build_sandbox_mock_incidents(health_only, all_tests_pass=False, failing_tests=2)
    health = next(i for i in incidents if i["bug_key"] == "health")
    assert health["status"] == "active"
    assert "patched" in health["agent_status"] or "waiting" in health["agent_status"]
    assert counts["active"] == 3


def test_incidents_resolve_when_all_tests_pass():
    fixed = "all good"
    incidents, counts = build_sandbox_mock_incidents(fixed, all_tests_pass=True, failing_tests=0)
    checkout = [i for i in incidents if i.get("bug_key")]
    assert all(i["status"] == "resolved" for i in checkout)
    assert counts["active"] == 0
