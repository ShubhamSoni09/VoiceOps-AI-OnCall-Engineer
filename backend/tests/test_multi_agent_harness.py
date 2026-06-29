from multi_agent_harness import MultiAgentHarnessReport, print_multi_agent_report, run_multi_agent_harness


def test_multi_agent_harness_runs_full_closure(tmp_path):
    report = run_multi_agent_harness(base_dir=tmp_path)

    assert isinstance(report, MultiAgentHarnessReport)
    assert report.route == "meeting_patch_closure"
    assert report.roles == ["coordinator", "meeting", "memory", "code", "review", "code", "review", "test", "git"]
    assert report.exchange_count >= 7
    assert report.revision_exchange_seen is True
    assert report.control_budget == 20
    assert report.control_timeout_seconds == 60.0
    assert report.action_id
    assert report.plan_approval_required is True
    assert report.approval_policy_mode == "preview_first"
    assert report.approval_llm_provider == "mock"
    assert report.approval_llm_fallback is True
    assert report.approval_multi_agent_run_id == report.run_id
    assert report.dashboard_pending_approval_count == 1
    assert report.dashboard_completed_patch_count == 1
    assert report.dashboard_pending_ready_state == "needs_attention"
    assert report.dashboard_completed_ready_state == "ready"
    assert report.dashboard_branch == report.branch
    assert report.dashboard_approver == "Sam Ortiz"
    assert report.revision_count == 1
    assert report.preapproval_tests_passed is True
    assert report.branch.startswith(f"voiceops/{report.action_id}-")
    assert report.tests_passed is True


def test_multi_agent_harness_prints_json(tmp_path, capsys):
    report = run_multi_agent_harness(base_dir=tmp_path)

    print_multi_agent_report(report, as_json=True)

    output = capsys.readouterr().out
    assert '"route": "meeting_patch_closure"' in output
    assert '"revision_count": 1' in output
    assert '"revision_exchange_seen": true' in output
    assert '"control_budget": 20' in output
    assert '"plan_approval_required": true' in output
    assert '"approval_policy_mode": "preview_first"' in output
    assert '"approval_llm_provider": "mock"' in output
    assert '"dashboard_pending_approval_count": 1' in output
    assert '"dashboard_completed_patch_count": 1' in output
    assert '"dashboard_completed_ready_state": "ready"' in output
    assert '"dashboard_approver": "Sam Ortiz"' in output
    assert '"preapproval_tests_passed": true' in output
    assert '"tests_passed": true' in output
