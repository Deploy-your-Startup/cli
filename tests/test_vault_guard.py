"""Tests for the bootstrap vault safety guards.

These cover the regression where a half-finished bootstrap left the vault
sealed with the public ``TEMPLATE_VAULT_PASSWORD`` while a different password
was written to the Keychain — and nothing noticed.
"""

import subprocess
from pathlib import Path

import click
import pytest

from cli.wizard import vault_guard

TEMPLATE = "ranhah-ceqZu9-fihfez"
NEW = "brand-new-rotated-password"


def _patch_decrypt(monkeypatch, decryptable):
    """decryptable: dict mapping password -> set of file names that decrypt."""

    def fake_check(path: Path, password: str) -> bool:
        return Path(path).name in decryptable.get(password, set())

    monkeypatch.setattr(vault_guard, "check_can_decrypt_with_password", fake_check)


def _patch_files(monkeypatch, names):
    files = [Path("/repo/deployment") / n for n in names]
    monkeypatch.setattr(vault_guard, "iter_vault_files", lambda _d: files)


# --- verify_rotation ---------------------------------------------------------


def test_verify_rotation_passes_when_sealed_with_new_only(monkeypatch):
    _patch_files(monkeypatch, ["production.yml", "all.yml"])
    _patch_decrypt(monkeypatch, {NEW: {"production.yml", "all.yml"}})
    # Should not raise.
    vault_guard.verify_rotation(Path("/repo/deployment"), NEW, TEMPLATE)


def test_verify_rotation_raises_when_still_template_decryptable(monkeypatch):
    _patch_files(monkeypatch, ["production.yml"])
    _patch_decrypt(
        monkeypatch,
        {NEW: {"production.yml"}, TEMPLATE: {"production.yml"}},
    )
    with pytest.raises(click.ClickException) as exc:
        vault_guard.verify_rotation(Path("/repo/deployment"), NEW, TEMPLATE)
    assert "Template-Passwort" in str(exc.value)


def test_verify_rotation_raises_when_new_cannot_decrypt(monkeypatch):
    _patch_files(monkeypatch, ["production.yml"])
    _patch_decrypt(monkeypatch, {TEMPLATE: {"production.yml"}})
    with pytest.raises(click.ClickException) as exc:
        vault_guard.verify_rotation(Path("/repo/deployment"), NEW, TEMPLATE)
    assert "neuen Passwort" in str(exc.value)


def test_verify_rotation_raises_when_no_vault_files(monkeypatch):
    _patch_files(monkeypatch, [])
    _patch_decrypt(monkeypatch, {})
    with pytest.raises(click.ClickException) as exc:
        vault_guard.verify_rotation(Path("/repo/deployment"), NEW, TEMPLATE)
    assert "keine Vault-Dateien" in str(exc.value)


# --- vault_is_decryptable ----------------------------------------------------


def test_vault_is_decryptable_true_when_all_files_decrypt(monkeypatch):
    _patch_files(monkeypatch, ["production.yml", "all.yml"])
    _patch_decrypt(monkeypatch, {NEW: {"production.yml", "all.yml"}})
    assert vault_guard.vault_is_decryptable(Path("/repo/deployment"), NEW) is True


def test_vault_is_decryptable_false_when_one_file_fails(monkeypatch):
    _patch_files(monkeypatch, ["production.yml", "all.yml"])
    _patch_decrypt(monkeypatch, {NEW: {"production.yml"}})
    assert vault_guard.vault_is_decryptable(Path("/repo/deployment"), NEW) is False


def test_vault_is_decryptable_false_without_password(monkeypatch):
    _patch_files(monkeypatch, ["production.yml"])
    _patch_decrypt(monkeypatch, {NEW: {"production.yml"}})
    assert vault_guard.vault_is_decryptable(Path("/repo/deployment"), None) is False


def test_vault_is_decryptable_false_when_no_vault_files(monkeypatch):
    _patch_files(monkeypatch, [])
    assert vault_guard.vault_is_decryptable(Path("/repo/deployment"), NEW) is False


# --- keychain helpers --------------------------------------------------------


def test_store_keychain_password_updates_existing(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(vault_guard.subprocess, "run", fake_run)
    vault_guard.store_keychain_password("hallo", "s3cret")
    assert "add-generic-password" in captured["cmd"]
    assert "-U" in captured["cmd"]
    assert "VAULT_PASSWORD_HALLO" in captured["cmd"]
    assert "s3cret" in captured["cmd"]


def test_read_keychain_password_returns_value(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="s3cret\n", stderr="")

    monkeypatch.setattr(vault_guard.subprocess, "run", fake_run)
    assert vault_guard.read_keychain_password("hallo") == "s3cret"


def test_read_keychain_password_returns_none_when_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(vault_guard.subprocess, "run", fake_run)
    assert vault_guard.read_keychain_password("hallo") is None
