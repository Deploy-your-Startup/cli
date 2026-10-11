from subprocess import CompletedProcess

from click.testing import CliRunner

from cli import preflight, sync_commands
from cli.startup import cli


def test_doctor_checks_only_and_redacts_gh_output(monkeypatch):
    monkeypatch.setattr(preflight, "local_checks", lambda: [(True, "local setup")])
    monkeypatch.setattr(preflight.shutil, "which", lambda name: name)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 1, "private-token", "private-token")

    monkeypatch.setattr(preflight.subprocess, "run", run)
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert "private-token" not in result.output
    assert calls == [["gh", "auth", "status"]]


def test_sync_keeps_https_remote(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, " M README.md", "")

    monkeypatch.setattr(sync_commands, "_run_command", run)
    assert sync_commands._commit_and_push(tmp_path, "sync")
    assert ["git", "push", "origin", "main"] in calls
    assert not any("set-url" in command for command in calls)
