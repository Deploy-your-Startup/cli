from subprocess import CompletedProcess
from types import SimpleNamespace

from click.testing import CliRunner

from cli import preflight, sync_commands
from cli.startup import cli
from cli.wizard.runner import FULLSTACK_STEPS, PITCH_STEPS
from cli.wizard.steps import shared_deployment


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


def test_bootstrap_syncs_before_creating_cloud_resources(monkeypatch):
    assert FULLSTACK_STEPS[0] is shared_deployment.SharedDeploymentStep
    assert PITCH_STEPS[0] is shared_deployment.SharedDeploymentStep
    step = shared_deployment.SharedDeploymentStep()
    ctx = SimpleNamespace(github_username="new-user")
    monkeypatch.setattr(shared_deployment, "repo_exists", lambda name: False)
    calls = []
    monkeypatch.setattr(
        shared_deployment, "sync_deploy_repo", lambda **kwargs: calls.append(kwargs)
    )
    assert step.check(ctx) is False
    step.run(ctx)
    assert calls == [{"owner": "new-user"}]


def test_bootstrap_keeps_existing_shared_repository(monkeypatch):
    monkeypatch.setattr(shared_deployment, "repo_exists", lambda name: True)
    assert shared_deployment.SharedDeploymentStep().check(
        SimpleNamespace(github_username="existing-user")
    )
