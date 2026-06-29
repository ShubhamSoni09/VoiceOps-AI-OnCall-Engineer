from prompt_injection_harness import (
    PromptInjectionHarnessReport,
    print_prompt_injection_report,
    run_prompt_injection_harness,
)


def test_prompt_injection_harness_blocks_jailbreak_paths(tmp_path):
    report = run_prompt_injection_harness(base_dir=tmp_path)

    assert isinstance(report, PromptInjectionHarnessReport)
    assert report.room_id == "main"
    assert report.workspace_changed_before_approval is False
    assert report.viewer_approve_status == 403
    assert report.viewer_room_status == 403
    assert report.secret_file_status != 200
    assert report.secret_search_hits == 0
    assert "memory_rag_read_only" in report.invariants_passed
    assert "mixed_code_change_route_requires_approval" in report.invariants_passed
    assert "multi_agent_preview_first" in report.invariants_passed
    assert "auto_external_env_sanitized" in report.invariants_passed
    assert "auto_external_preview_first" in report.invariants_passed
    assert "auto_external_status_claims_ignored" in report.invariants_passed
    assert "external_cli_env_sanitized" in report.invariants_passed
    assert "external_cli_preview_first" in report.invariants_passed
    assert "external_cli_status_claims_ignored" in report.invariants_passed
    assert "room_snapshot_requires_membership" in report.invariants_passed
    assert "room_scoped_tool_surfaces_require_membership" in report.invariants_passed
    assert "project_restricted_room_access" in report.invariants_passed
    assert "agent_tool_prompt_injection_self_approves" in report.blocked_attack_paths
    assert "cross_room_collaboration_data_read" in report.blocked_attack_paths
    assert "cross_room_agent_speaker_ontology_access" in report.blocked_attack_paths
    assert "cross_project_room_access" in report.blocked_attack_paths
    assert "external_cli_reads_service_environment" in report.blocked_attack_paths
    assert "auto_external_agent_reads_service_environment" in report.blocked_attack_paths
    assert "auto_external_agent_prompt_injection_bypasses_approval" in report.blocked_attack_paths
    assert "auto_external_agent_forges_approval_metadata" in report.blocked_attack_paths
    assert "external_agent_forges_approval_metadata" in report.blocked_attack_paths
    assert report.auto_external_secret_leaked is False
    assert report.auto_external_claimed_approval_ignored is True
    assert report.auto_external_runtime["execution_mode"] == "local_cli_patch"
    assert report.auto_external_runtime["env_policy"] == "minimal_external_agent_env"
    assert "secret-visible=no" in report.auto_external_runtime["stdout"]
    assert "VOICEOPS_ACTION_STATUS=completed" in report.auto_external_runtime["stdout"]
    assert "VOICEOPS_APPROVED_BY=Bob" in report.auto_external_runtime["stdout"]
    assert report.external_agent_secret_leaked is False
    assert report.room_snapshot_secret_leaked is False
    assert report.external_agent_claimed_approval_ignored is True
    assert report.external_agent_runtime["execution_mode"] == "local_cli_patch"
    assert report.external_agent_runtime["env_policy"] == "minimal_external_agent_env"
    assert "secret-visible=no" in report.external_agent_runtime["stdout"]
    assert "VOICEOPS_ACTION_STATUS=completed" in report.external_agent_runtime["stdout"]
    assert "VOICEOPS_APPROVED_BY=Bob" in report.external_agent_runtime["stdout"]
    assert report.route_policy["action_policy"] == "approval_required_before_workspace_write"
    assert report.trace_coverage["patch_proposal"] is True
    assert report.room_endpoint_statuses
    assert report.room_endpoint_statuses["POST /voice/process-audio"] == 403
    assert report.room_endpoint_statuses["POST /voice/process-text"] == 403
    assert set(report.room_endpoint_statuses.values()) == {403}


def test_prompt_injection_harness_prints_json(tmp_path, capsys):
    report = run_prompt_injection_harness(base_dir=tmp_path)

    print_prompt_injection_report(report, as_json=True)

    output = capsys.readouterr().out
    assert '"blocked_attack_paths"' in output
    assert '"viewer_approve_status": 403' in output
    assert '"viewer_room_status": 403' in output
    assert '"room_endpoint_statuses"' in output
    assert '"POST /voice/process-audio": 403' in output
    assert '"secret_search_hits": 0' in output
    assert '"external_agent_secret_leaked": false' in output
    assert '"auto_external_secret_leaked": false' in output
    assert '"auto_external_claimed_approval_ignored": true' in output
    assert '"room_snapshot_secret_leaked": false' in output
    assert '"external_agent_claimed_approval_ignored": true' in output
    assert report.voice_action_id in output
    assert report.auto_external_action_id in output
    assert report.external_agent_action_id in output
