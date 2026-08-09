"""Tests for the three ways a secret used to be harder to store than it should be.

Before these, `startup secrets update` could only replace a vault block that
already existed, took the vault password on the command line, and took values on
the command line too. So a new secret needed a hand-written script, and both the
password and the value were visible to anyone who could run `ps`.
"""

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.startup import cli
from cli.update_vault_secrets import update_secrets
from cli.vault.fields import get_inline_vault_value

PASSWORD = "test-vault-password"


@pytest.fixture
def group_vars():
    """A group_vars file with one existing vault field and one plain value."""
    directory = Path(tempfile.mkdtemp())
    path = directory / "all.yml"
    path.write_text(
        "project_name: example\n"
        "existing_secret: !vault |\n"
        "  $ANSIBLE_VAULT;1.1;AES256\n"
        "  " + "31" * 32 + "\n"
    )
    yield path
    for leftover in directory.glob("*"):
        leftover.unlink()
    directory.rmdir()


def test_missing_field_is_an_error_instead_of_a_silent_no_op(group_vars):
    # GIVEN a field that has no vault block anywhere
    # WHEN it is set without saying where it should be created
    success, _updated, _ = update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("brand_new_secret", "s3cret")],
    )

    # THEN the command fails and writes nothing, rather than reporting success
    # for a secret it never stored
    assert success is False
    assert "brand_new_secret" not in group_vars.read_text()


def test_create_in_adds_a_new_vault_field(group_vars):
    # GIVEN a field that does not exist yet
    # WHEN --create-in names the file it belongs in
    success, _updated, _ = update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("brand_new_secret", "s3cret")],
        create_in=str(group_vars),
    )

    # THEN it is appended as an encrypted block that decrypts to the value given
    assert success is not False
    assert get_inline_vault_value(group_vars, "brand_new_secret", PASSWORD) == "s3cret"

    # AND the field that was already there is untouched
    assert "existing_secret: !vault |" in group_vars.read_text()


def test_created_field_is_indented_like_a_replaced_one(group_vars):
    # GIVEN one field created from scratch and one replaced in place.
    # `ansible-vault encrypt_string` lines its ciphertext up under the variable
    # name, so the indentation depends on how long that name is. The replace
    # path normalised it; the create path passed it through, and files ended up
    # looking hand-edited by two different people.
    update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[
            ("a_short_one", "value"),
            ("a_considerably_longer_field_name", "value"),
        ],
        create_in=str(group_vars),
    )
    update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("a_short_one", "replaced")],
    )

    # THEN every ciphertext line sits two spaces in, whatever the name's length
    # and whichever path wrote it
    ciphertext = [
        line
        for line in group_vars.read_text().splitlines()
        if line.strip().startswith(("$ANSIBLE_VAULT", "3", "6"))
    ]
    assert ciphertext, "expected encrypted lines to inspect"
    assert {len(line) - len(line.lstrip()) for line in ciphertext} == {2}


def test_create_in_leaves_existing_fields_to_the_normal_path(group_vars):
    # GIVEN one existing field and one new one in the same invocation
    success, _, _ = update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("existing_secret", "updated"), ("brand_new_secret", "created")],
        create_in=str(group_vars),
    )

    # THEN the existing block is replaced in place and the new one appended,
    # and the file still holds exactly one of each
    assert success is not False
    text = group_vars.read_text()
    assert text.count("existing_secret: !vault |") == 1
    assert text.count("brand_new_secret: !vault |") == 1
    assert get_inline_vault_value(group_vars, "existing_secret", PASSWORD) == "updated"
    assert get_inline_vault_value(group_vars, "brand_new_secret", PASSWORD) == "created"


def test_only_existing_still_skips_missing_fields_quietly(group_vars):
    # GIVEN --only-existing, which means "skip what is not there" on purpose
    success, _, _ = update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("brand_new_secret", "s3cret")],
        only_existing=True,
    )

    # THEN the new error path does not fire - that flag is the caller saying
    # the absence is expected
    assert success is not False
    assert "brand_new_secret" not in group_vars.read_text()


def test_field_stdin_keeps_the_value_out_of_the_arguments(group_vars):
    # GIVEN a secret piped in rather than passed as an argument
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "secrets",
            "update",
            "-r",
            str(group_vars),
            "-p",
            PASSWORD,
            "--field-stdin",
            "piped_secret",
            "--create-in",
            str(group_vars),
        ],
        input="from-stdin\n",
    )

    # THEN it is stored, and the trailing newline the shell adds is not part of it
    assert result.exit_code == 0, result.output
    assert get_inline_vault_value(group_vars, "piped_secret", PASSWORD) == "from-stdin"


def test_field_stdin_rejects_empty_input(group_vars):
    # GIVEN nothing on stdin
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "secrets",
            "update",
            "-r",
            str(group_vars),
            "-p",
            PASSWORD,
            "--field-stdin",
            "piped_secret",
            "--create-in",
            str(group_vars),
        ],
        input="",
    )

    # THEN it says so rather than storing an empty secret
    assert result.exit_code != 0
    assert "stdin was empty" in result.output


def test_vault_password_falls_back_to_the_keychain(group_vars, monkeypatch):
    # GIVEN no --vault-password on the command line
    asked = {}

    class FakeBackend:
        def read(self, key):
            asked["key"] = key
            return PASSWORD

    monkeypatch.setattr(
        "cli.ansible_commands.get_backend", lambda *a, **kw: FakeBackend()
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "secrets",
            "update",
            "-r",
            str(group_vars),
            "--field-set",
            "existing_secret",
            "via-keychain",
        ],
    )

    # THEN the password came from the backend, keyed by the project the path
    # belongs to, and the secret was still written
    assert result.exit_code == 0, result.output
    assert asked["key"] == group_vars.parent.name
    assert (
        get_inline_vault_value(group_vars, "existing_secret", PASSWORD)
        == "via-keychain"
    )


def test_get_field_falls_back_to_the_keychain(group_vars, monkeypatch):
    # GIVEN a stored secret and no --vault-password on the command line.
    # `secrets update` learned the keychain fallback first; reading a field back
    # still demanded the password, so the two halves of the same workflow
    # disagreed about where the password comes from.
    update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("stored_secret", "the-value")],
        create_in=str(group_vars),
    )

    class FakeBackend:
        def read(self, key):
            return PASSWORD

    monkeypatch.setattr(
        "cli.ansible_commands.get_backend", lambda *a, **kw: FakeBackend()
    )

    # WHEN
    runner = CliRunner()
    result = runner.invoke(
        cli, ["secrets", "get-field", "-f", str(group_vars), "--field", "stored_secret"]
    )

    # THEN
    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[-1] == "the-value"


def test_update_inline_field_falls_back_to_the_keychain(group_vars, monkeypatch):
    # GIVEN an existing block and no --vault-password
    update_secrets(
        repo=str(group_vars),
        vault_password=PASSWORD,
        set_field=[("stored_secret", "before")],
        create_in=str(group_vars),
    )

    class FakeBackend:
        def read(self, key):
            return PASSWORD

    monkeypatch.setattr(
        "cli.ansible_commands.get_backend", lambda *a, **kw: FakeBackend()
    )

    # WHEN
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "secrets",
            "update-inline-field",
            "-f",
            str(group_vars),
            "--field",
            "stored_secret",
            "--value",
            "after",
        ],
    )

    # THEN
    assert result.exit_code == 0, result.output
    assert get_inline_vault_value(group_vars, "stored_secret", PASSWORD) == "after"


def test_password_scope_walks_up_to_the_deployment_boundary(tmp_path):
    # GIVEN the layout every project here uses
    from cli.startup import _password_scope

    deployment = tmp_path / "about-phil" / "deployment"
    (deployment / "group_vars").mkdir(parents=True)
    all_yml = deployment / "group_vars" / "all.yml"
    all_yml.write_text("project_name: about-phil\n")

    # THEN every way of pointing at the vault resolves to the same project,
    # rather than to "group_vars" or to the file name
    assert Path(_password_scope(str(all_yml))).name == "deployment"
    assert Path(_password_scope(str(deployment / "group_vars"))).name == "deployment"
    assert Path(_password_scope(str(deployment))).name == "deployment"


def test_scan_skips_vendored_directories(tmp_path):
    # GIVEN a project whose virtualenv holds far more YAML than the project does
    from cli.update_vault_secrets import find_yaml_files

    (tmp_path / "group_vars").mkdir()
    (tmp_path / "group_vars" / "all.yml").write_text("a: 1\n")
    for vendored in (".venv", "node_modules", ".git"):
        (tmp_path / vendored / "deep").mkdir(parents=True)
        (tmp_path / vendored / "deep" / "noise.yml").write_text("b: 2\n")

    # THEN only the project's own file is scanned - this is what made `-r .`
    # look like it had hung
    found = [p.name for p in find_yaml_files(tmp_path)]
    assert found == ["all.yml"]
