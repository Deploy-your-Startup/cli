"""Exercise personal SSH-key selection against real OpenSSH key files."""

import subprocess

import click
import pytest

from cli.wizard.base import prompt_user_public_key


def test_explicit_custom_public_key_is_usable_without_default_key_files(tmp_path):
    # GIVEN a real OpenSSH key with a custom filename.
    key = tmp_path / "server-access"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    public_key = key.with_suffix(".pub")
    # WHEN selecting it for unattended bootstrap.
    result = prompt_user_public_key(non_interactive=True, public_key_path=public_key)
    # THEN the actual public key is returned; no private material is exposed.
    assert result == public_key.read_text().strip()
    assert "PRIVATE" not in result


@pytest.mark.parametrize(
    "content",
    ["ssh-ed25519 not-a-key", "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-material"],
)
def test_invalid_or_private_key_is_rejected_without_leaking_contents(
    tmp_path, capsys, content
):
    # GIVEN a malformed or accidentally selected private key file.
    key = tmp_path / "wrong.pub"
    key.write_text(content)
    # WHEN explicitly selecting it through the bootstrap key interface.
    with pytest.raises(click.ClickException, match="public key file"):
        prompt_user_public_key(public_key_path=key)
    # THEN the error is actionable and neither key material nor a fingerprint is printed.
    output = capsys.readouterr()
    assert content not in output.out + output.err
