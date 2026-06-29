from __future__ import annotations

import textwrap

import pytest

from app.agent_runtime import AgentRuntimeService
from app.config import Settings
from app.workspace.tools import WorkspaceError


def _workspace(path):
    path.mkdir()
    (path / "app.py").write_text(
        textwrap.dedent(
            """
            def health():
                return "down"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (path / "test_app.py").write_text(
        textwrap.dedent(
            """
            from app import health


            def test_health():
                assert health() == "ok"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_agent_runtime_validates_patch_in_temporary_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _workspace(workspace)
    settings = Settings(voiceops_workspace=str(workspace))
    runtime = AgentRuntimeService(settings)

    result = runtime.validate_proposed_patch(
        proposed_files={
            "app.py": textwrap.dedent(
                """
                def health():
                    return "ok"
                """
            ).strip()
            + "\n"
        },
        command="python -m pytest -q",
    )

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["workspace"] == "temporary"
    assert result["runtime"] == "isolated_temp_workspace"
    assert result["exit_code"] == 0
    assert result["files_changed"] == ["app.py"]
    assert "return \"ok\"" in result["diff"]
    assert "return \"down\"" in (workspace / "app.py").read_text(encoding="utf-8")


def test_agent_runtime_records_blocked_commands_without_shell_execution(tmp_path):
    workspace = tmp_path / "workspace"
    _workspace(workspace)
    settings = Settings(voiceops_workspace=str(workspace))
    runtime = AgentRuntimeService(settings)

    result = runtime.validate_proposed_patch(
        proposed_files={"app.py": "def health():\n    return \"ok\"\n"},
        command="python -m pytest -q && rm -rf .",
    )

    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["exit_code"] == 126
    assert result["blocked"] is True
    assert "blocked operators" in result["stderr"]


def test_agent_runtime_blocks_legacy_template_without_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _workspace(workspace)
    settings = Settings(voiceops_workspace=str(workspace))
    runtime = AgentRuntimeService(settings)

    result = runtime.run_read_only_command(
        command="python agent.py",
        command_template="python agent.py --prompt {prompt}",
        template_vars={"prompt": "review app.py"},
    )

    assert result["status"] == "failed"
    assert result["blocked"] is True
    assert "must include {workspace}" in result["stderr"]


def test_agent_runtime_rejects_proposed_file_that_escapes_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    _workspace(workspace)
    settings = Settings(voiceops_workspace=str(workspace))
    runtime = AgentRuntimeService(settings)

    with pytest.raises(WorkspaceError, match="escapes workspace"):
        runtime.validate_proposed_patch(
            proposed_files={"../outside.py": "print('no')\n"},
            command="python -m pytest -q",
        )
