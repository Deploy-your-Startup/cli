"""Integration checks with real encrypted files and the startup CLI."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from cli.wizard import vault_guard

TEMPLATE = "synthetic-template-password"
NEW = "synthetic-rotated-password"


def encrypted_file(folder, filename, password):
    path = folder / filename
    path.write_text("label: example\n")
    command = [
        sys.executable,
        "-m",
        "cli.startup",
        "secrets",
        "update",
        "-r",
        str(path),
        "-p",
        password,
        "--field-stdin",
        "secret",
        "--create-in",
        str(path),
    ]
    for extra in (["--dry-run"], []):
        result = subprocess.run(
            command + extra,
            input="synthetic-value",
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            },
        )
        assert result.returncode == 0, result.stderr
    return path


def test_rotation_accepts_real_files_with_new_password(tmp_path):
    # GIVEN two vault files encrypted through the public CLI.
    encrypted_file(tmp_path, "production.yml", NEW)
    encrypted_file(tmp_path, "all.yml", NEW)
    # WHEN the rotation guard reads and decrypts both files.
    vault_guard.verify_rotation(tmp_path, NEW, TEMPLATE)
    # THEN all files are usable with the new password only.
    assert vault_guard.vault_is_decryptable(tmp_path, NEW)
    assert not vault_guard.vault_is_decryptable(tmp_path, TEMPLATE)


def test_rotation_rejects_unchanged_template_password(tmp_path):
    # GIVEN a vault still using the template password.
    encrypted_file(tmp_path, "production.yml", TEMPLATE)
    # WHEN rotation reports the same password as the new one.
    # THEN the real guard rejects it with an English diagnostic.
    with pytest.raises(click.ClickException, match="template password"):
        vault_guard.verify_rotation(tmp_path, TEMPLATE, TEMPLATE)


def test_rotation_rejects_mixed_passwords(tmp_path):
    # GIVEN one rotated file and one forgotten file.
    encrypted_file(tmp_path, "production.yml", NEW)
    encrypted_file(tmp_path, "all.yml", TEMPLATE)
    # WHEN verifying the whole deployment.
    # THEN the failed file is identified and the deployment is not ready.
    with pytest.raises(click.ClickException, match=r"new password: all\.yml"):
        vault_guard.verify_rotation(tmp_path, NEW, TEMPLATE)
    assert not vault_guard.vault_is_decryptable(tmp_path, NEW)


def test_rotation_rejects_missing_vault_files(tmp_path):
    # GIVEN a directory with no encrypted files.
    # WHEN checking rotation or decryptability.
    # THEN empty state is never treated as a successful rotation.
    with pytest.raises(click.ClickException, match="no vault files"):
        vault_guard.verify_rotation(tmp_path, NEW, TEMPLATE)
    assert not vault_guard.vault_is_decryptable(tmp_path, NEW)
    assert not vault_guard.vault_is_decryptable(tmp_path, None)


# --- keychain helpers --------------------------------------------------------


def test_store_keychain_password_uses_safe_backend(monkeypatch):
    captured = []
    monkeypatch.setattr(
        vault_guard,
        "get_backend",
        lambda: SimpleNamespace(write=lambda *args: captured.append(args)),
    )
    vault_guard.store_keychain_password("hallo", "s3cret")
    assert captured == [("hallo", "s3cret")]


def test_read_keychain_password_returns_value(monkeypatch):
    monkeypatch.setattr(
        vault_guard, "get_backend", lambda: SimpleNamespace(read=lambda key: "s3cret")
    )
    assert vault_guard.read_keychain_password("hallo") == "s3cret"


def test_read_keychain_password_returns_none_when_missing(monkeypatch):
    def missing(key):
        raise click.ClickException("Not found")

    monkeypatch.setattr(
        vault_guard, "get_backend", lambda: SimpleNamespace(read=missing)
    )
    assert vault_guard.read_keychain_password("hallo") is None
