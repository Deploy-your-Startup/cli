from subprocess import CompletedProcess

import click
import pytest

from cli.wizard.steps.finalize import set_github_vault_secret


def test_github_secret_uses_stdin_and_redacts_failure(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 1, "", "secret-value")

    monkeypatch.setattr("cli.wizard.steps.finalize.subprocess.run", run)
    with pytest.raises(click.ClickException) as error:
        set_github_vault_secret(tmp_path, "secret-value")
    command, kwargs = calls[0]
    assert "secret-value" not in command
    assert kwargs["input"] == "secret-value"
    assert "secret-value" not in str(error.value)
