from meeting_closure_harness import (
    HarnessReport,
    PullRequestRoomHarnessReport,
    print_harness_report,
    run_meeting_closure_harness,
    run_pull_request_room_harness,
)


def test_meeting_closure_harness_runs_full_approval_and_reject_flow(tmp_path):
    report = run_meeting_closure_harness(base_dir=tmp_path, include_reject_path=True)

    assert isinstance(report, HarnessReport)
    assert report.room_id == "main"
    assert report.collab_backend == "json"
    assert report.event_store_ready is False
    assert report.speaker_mappings == {
        "SPEAKER_00": "Priya Nair",
        "SPEAKER_01": "Sam Ortiz",
    }
    assert "health check" in report.memory_answer.lower()
    assert report.external_assignment_id
    assert report.external_action_id
    assert report.external_provider == "local"
    assert report.external_auto_dispatched is True
    assert report.external_pending_seen_by_bob is True
    assert report.pending_seen_by_alice is True
    assert report.pending_seen_by_bob is True
    assert report.completed_seen_by_alice is True
    assert report.completed_seen_by_bob is True
    assert report.branch.startswith(f"voiceops/{report.action_id}-")
    assert report.files_changed == ["app.py"]
    assert report.tests_passed is True
    assert report.git_dirty_after is True
    assert report.trace_event_count >= 6
    assert report.trace_coverage["speaker_segments"] is True
    assert report.trace_coverage["patch_proposal"] is True
    assert report.trace_coverage["approval_decision"] is True
    assert report.trace_coverage["git_branch"] is True
    assert report.rejected_action_id
    assert any("Rejected by Sam Ortiz" in line for line in report.rejected_handoff_lines)
    assert any("Approved by Sam Ortiz" in line for line in report.handoff_lines)
    assert any("Branch: voiceops/" in line for line in report.handoff_lines)


def test_meeting_closure_harness_can_run_on_sqlite_event_store(tmp_path):
    report = run_meeting_closure_harness(
        base_dir=tmp_path,
        include_reject_path=True,
        collab_backend="sqlite",
    )

    assert report.collab_backend == "sqlite"
    assert report.event_store_ready is True
    assert report.event_count > 0
    assert report.event_types["message.appended"] >= 2
    assert report.event_types["action.appended"] >= 2
    assert report.event_types["action.updated"] >= 2
    assert report.trace_coverage["event_store"] is True
    assert "event_store" not in report.trace_missing
    assert report.external_auto_dispatched is True
    assert report.external_provider == "local"
    assert report.pending_seen_by_bob is True
    assert report.completed_seen_by_alice is True
    assert any("Approved by Sam Ortiz" in line for line in report.handoff_lines)


def test_room_pull_request_harness_proves_room_workspace_pr_path(tmp_path):
    report = run_pull_request_room_harness(base_dir=tmp_path)

    assert isinstance(report, PullRequestRoomHarnessReport)
    assert report.room_id == "main"
    assert report.ready is True
    assert report.remote_url == "git@github.com:room/app.git"
    assert report.web_url == "https://github.com/room/app"
    assert report.workspace_source == "room"
    assert report.workspace_path == report.room_workspace_path
    assert report.global_workspace_unchanged is True
    assert report.branch.startswith(f"voiceops/{report.action_id}-")
    assert report.commit_sha


def test_meeting_closure_harness_prints_json_report(tmp_path, capsys):
    report = run_meeting_closure_harness(
        base_dir=tmp_path,
        include_reject_path=False,
        collab_backend="sqlite",
    )

    print_harness_report(report, as_json=True)

    output = capsys.readouterr().out
    assert '"room_id": "main"' in output
    assert '"collab_backend": "sqlite"' in output
    assert '"event_store_ready": true' in output
    assert '"tests_passed": true' in output
    assert '"external_provider": "local"' in output
    assert '"external_auto_dispatched": true' in output
    assert '"pending_seen_by_bob": true' in output
    assert '"completed_seen_by_alice": true' in output
    assert '"trace_coverage"' in output
    assert '"event_store": true' in output
    assert report.action_id in output
