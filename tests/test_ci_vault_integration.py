"""GIVEN a CI password environment, WHEN the CLI reads a file, THEN argv stays clean."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ansible.parsing.vault import VaultLib, VaultSecret

SOURCE = Path(__file__).resolve().parents[1] / "src"
PASSWORD = "integration-ci-password-only"


@pytest.mark.parametrize("valid", [True, False])
def test_vault_file_streams_through_the_cli_without_password_arguments(tmp_path, valid):
    # GIVEN a real encrypted Vault file and a password only in the environment.
    value = b"integration-private-key-content\n"
    path = tmp_path / "ci_ssh_key"
    path.write_bytes(
        VaultLib([("default", VaultSecret(PASSWORD.encode()))]).encrypt(value)
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
        "STARTUP_VAULT_PASSWORD": PASSWORD if valid else "wrong-test-password",
    }
    # WHEN the real CLI decrypts it without passing credentials as options.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "secrets", "get-file", "-f", str(path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        timeout=60,
    )
    # THEN stdout is pipeable, failures leak neither data nor password, and no temp data remains.
    assert (result.returncode == 0) is valid
    assert result.stdout == (value if valid else b"")
    assert PASSWORD.encode() not in result.stderr
    assert value not in result.stderr
    assert list(tmp_path.iterdir()) == [path]
