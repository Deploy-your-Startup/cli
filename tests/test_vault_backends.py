"""Tests for the pluggable vault password backends."""

import subprocess
from types import SimpleNamespace

import click
import pytest

from cli import vault_backends
from cli.vault_backends import (
    KeychainBackend,
    get_backend,
    keychain_service_name,
)


def test_keychain_service_name_mapping():
    assert (
        keychain_service_name("gaming-buch-club") == "VAULT_PASSWORD_GAMING_BUCH_CLUB"
    )
    assert keychain_service_name("about-phil") == "VAULT_PASSWORD_ABOUT_PHIL"


def test_get_backend_returns_keychain_by_default():
    backend = get_backend()
    assert isinstance(backend, KeychainBackend)
    assert backend.name == "keychain"


def test_get_backend_unknown_raises_with_available_list():
    with pytest.raises(click.ClickException) as exc:
        get_backend("does-not-exist")
    assert "keychain" in str(exc.value)


def test_keychain_backend_read_returns_password(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="s3cret\n", stderr="")

    monkeypatch.setattr(vault_backends.subprocess, "run", fake_run)
    assert KeychainBackend().read("about-phil") == "s3cret"


def test_keychain_backend_read_missing_entry_raises_helpful(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(vault_backends.subprocess, "run", fake_run)
    with pytest.raises(click.ClickException) as exc:
        KeychainBackend().read("about-phil")
    assert "VAULT_PASSWORD_ABOUT_PHIL" in str(exc.value)


def test_resolve_vault_password_explicit_wins_without_backend(monkeypatch):
    from cli import ansible_commands

    def boom(*args, **kwargs):
        raise AssertionError("backend must not be consulted when password is explicit")

    monkeypatch.setattr(ansible_commands, "get_backend", boom)
    assert (
        ansible_commands.resolve_vault_password(
            vault_password="explicit", working_directory="."
        )
        == "explicit"
    )


def test_write_never_sends_password_to_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vault_backends,
        "_macos_keyring",
        lambda: SimpleNamespace(set_password=lambda *args: calls.append(args)),
    )
    monkeypatch.setattr(
        vault_backends.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Password storage must use the native API"),
    )
    monkeypatch.setenv("USER", "tester")
    KeychainBackend().write("demo", "private-value")
    assert calls == [("VAULT_PASSWORD_DEMO", "tester", "private-value")]


def test_write_failure_does_not_expose_provider_error(monkeypatch):
    def fail(*args):
        raise vault_backends.KeyringError("private-value")

    monkeypatch.setattr(
        vault_backends, "_macos_keyring", lambda: SimpleNamespace(set_password=fail)
    )
    with pytest.raises(click.ClickException) as error:
        KeychainBackend().write("demo", "private-value")
    assert "private-value" not in str(error.value)
    assert error.value.__suppress_context__
