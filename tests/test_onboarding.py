from subprocess import CompletedProcess

from cli import sync_commands


def test_sync_keeps_https_remote(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, " M README.md", "")

    monkeypatch.setattr(sync_commands, "_run_command", run)
    assert sync_commands._commit_and_push(tmp_path, "sync")
    assert ["git", "push", "origin", "main"] in calls
    assert not any("set-url" in command for command in calls)
