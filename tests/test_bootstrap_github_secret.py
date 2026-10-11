"""GIVEN a GitHub boundary failure, WHEN setting a secret, THEN it stays private."""

import json
import sys

import click
import pytest

from cli.wizard.steps.finalize import set_github_vault_secret


def test_github_secret_uses_stdin_and_redacts_failure(monkeypatch, tmp_path):
    # GIVEN a real executable stand-in for the GitHub CLI boundary.
    commands = tmp_path / "commands"
    commands.mkdir()
    recorded = tmp_path / "request.json"
    gh = commands / "gh"
    gh.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
secret = sys.stdin.read()
Path({str(recorded)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'stdin': secret}}))
print(secret, file=sys.stderr)
sys.exit(1)
""")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands))

    # WHEN the real public entrypoint sends a synthetic vault password.
    with pytest.raises(click.ClickException) as error:
        set_github_vault_secret(tmp_path, "synthetic-secret-value")

    # THEN the provider receives stdin, while argv and the CLI error stay private.
    request = json.loads(recorded.read_text())
    assert request["argv"] == ["secret", "set", "VAULT_PASSWORD"]
    assert request["stdin"] == "synthetic-secret-value"
    assert "synthetic-secret-value" not in str(error.value)
