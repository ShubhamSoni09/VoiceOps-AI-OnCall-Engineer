from external_coding_agent_harness import (
    ExternalCodingAgentHarnessReport,
    print_harness_report,
    run_external_coding_agent_harness,
)


def test_external_coding_agent_harness_runs_full_preview_approval_flow(tmp_path):
    report = run_external_coding_agent_harness(base_dir=tmp_path)

    assert isinstance(report, ExternalCodingAgentHarnessReport)
    assert report.provider == "codex"
    assert report.pending_before_approval is True
    assert report.workspace_unchanged_before_approval is True
    assert report.room_workspace_isolated is True
    assert report.runtime_mode == "local_cli_patch"
    assert report.runtime_workspace == "temporary"
    assert report.requester == "Priya Nair"
    assert report.approver == "Sam Ortiz"
    assert report.branch.startswith(f"voiceops/{report.action_id}-")
    assert report.files_changed == ["app.py"]
    assert report.tests_passed is True
    assert any("Approved by Sam Ortiz" in line for line in report.handoff_lines)
    assert any("Branch: voiceops/" in line for line in report.handoff_lines)


def test_external_coding_agent_harness_runs_local_open_agent_flow(tmp_path):
    report = run_external_coding_agent_harness(provider="local", base_dir=tmp_path)

    assert isinstance(report, ExternalCodingAgentHarnessReport)
    assert report.provider == "local"
    assert report.pending_before_approval is True
    assert report.workspace_unchanged_before_approval is True
    assert report.room_workspace_isolated is True
    assert report.runtime_mode == "local_cli_patch"
    assert report.runtime_workspace == "temporary"
    assert report.branch.startswith(f"voiceops/{report.action_id}-")
    assert report.files_changed == ["app.py"]
    assert report.tests_passed is True
    assert any("Approved by Sam Ortiz" in line for line in report.handoff_lines)


def test_external_coding_agent_harness_runs_claude_code_flow(tmp_path):
    report = run_external_coding_agent_harness(provider="claude", base_dir=tmp_path)

    assert isinstance(report, ExternalCodingAgentHarnessReport)
    assert report.provider == "claude"
    assert report.pending_before_approval is True
    assert report.workspace_unchanged_before_approval is True
    assert report.room_workspace_isolated is True
    assert report.runtime_mode == "local_cli_patch"
    assert report.runtime_workspace == "temporary"
    assert report.branch.startswith(f"voiceops/{report.action_id}-")
    assert report.files_changed == ["app.py"]
    assert report.tests_passed is True
    assert any("Approved by Sam Ortiz" in line for line in report.handoff_lines)


def test_external_coding_agent_harness_prints_json_report(tmp_path, capsys):
    report = run_external_coding_agent_harness(base_dir=tmp_path)

    print_harness_report(report, as_json=True)

    output = capsys.readouterr().out
    assert '"provider": "codex"' in output
    assert '"runtime_mode": "local_cli_patch"' in output
    assert '"runtime_workspace": "temporary"' in output
    assert '"room_workspace_isolated": true' in output
    assert '"tests_passed": true' in output
    assert report.action_id in output
