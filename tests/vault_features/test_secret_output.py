"""Nothing secret may reach the terminal.

`verify_vault_password` used to put the password straight into its failure
message. With the keychain fallback that got worse than it looks: a password
the user never typed would surface in terminals, scrollback and CI logs the
moment a file did not match.

The second rule here is about stdout specifically. `secrets get-field` exists
to emit exactly one value, so anything else it has to say belongs on stderr -
otherwise every caller has to filter the chatter back out.
"""

import pytest
from click.testing import CliRunner

from cli.startup import cli
from cli.update_vault_secrets import update_secrets
from cli.vault.common import verify_vault_password
from cli.vault.fields import extract_vault_block

PASSWORD = "test-vault-password"
WRONG_PASSWORD = "definitely-not-it"


@pytest.fixture
def group_vars(tmp_path):
    """A file holding one real, decryptable vault field."""
    path = tmp_path / "all.yml"
    path.write_text("project_name: example\n")
    update_secrets(
        repo=str(path),
        vault_password=PASSWORD,
        set_field=[("stored_secret", "the-value")],
        create_in=str(path),
    )
    return path


def test_a_wrong_password_is_not_echoed(group_vars, capsys):
    # GIVEN a vault block and a password that cannot open it
    block = extract_vault_block(group_vars.read_text(), "stored_secret")
    assert block, "fixture should have produced a readable vault block"

    # WHEN
    assert verify_vault_password(block, WRONG_PASSWORD) is False

    # THEN the failure is reported without quoting what was tried
    captured = capsys.readouterr()
    assert WRONG_PASSWORD not in captured.out
    assert WRONG_PASSWORD not in captured.err
    assert "Failed to decrypt" in captured.err


def test_get_field_writes_only_the_value_to_stdout(group_vars):
    # GIVEN a caller that pipes the output somewhere
    runner = CliRunner()

    # WHEN
    result = runner.invoke(
        cli,
        [
            "secrets",
            "get-field",
            "-f",
            str(group_vars),
            "--field",
            "stored_secret",
            "-p",
            PASSWORD,
        ],
    )

    # THEN stdout carries the value and nothing else - no `| tail -1` needed
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "the-value"
