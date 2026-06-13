"""Pluggable backends for storing and retrieving the Ansible vault password.

A backend abstracts *where* the vault password lives. Today only the macOS
Keychain is implemented, but the indirection keeps the door open for others
(secret-tool/libsecret, an encrypted file, 1Password/pass via a command, a
GitHub Actions secret as a write-only target, ...) without touching the
command layer: every `startup ansible` command resolves its password through
`get_backend(...).read(project)`.

To add a backend, implement `PasswordBackend` and register it in `_BACKENDS`.
"""

from __future__ import annotations

import os
import subprocess
from typing import Protocol, runtime_checkable

import click

DEFAULT_VAULT_BACKEND = "keychain"


def keychain_service_name(project_name: str) -> str:
    return f"VAULT_PASSWORD_{project_name}".upper().replace("-", "_")


@runtime_checkable
class PasswordBackend(Protocol):
    """A place the vault password can be read from and written to.

    `key` is the project name; backends derive their own storage key from it
    (e.g. the Keychain service name). Backends that cannot return a value
    (write-only stores such as a GitHub Actions secret) should raise from
    `read`.
    """

    name: str

    def read(self, key: str) -> str: ...

    def write(self, key: str, value: str) -> None: ...


class KeychainBackend:
    """macOS Keychain via the `security` CLI."""

    name = "keychain"

    def read(self, key: str) -> str:
        service_name = keychain_service_name(key)
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    os.environ.get("USER", ""),
                    "-s",
                    service_name,
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise click.ClickException(
                "The macOS `security` tool is not available — the keychain "
                "vault backend only works on macOS. Pass --vault-password instead."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise click.ClickException(
                f'No vault password found in macOS Keychain for "{service_name}". '
                f"Store it first, for example from the portfolio hub with: "
                f"scripts/secrets.sh store {key}"
            ) from exc

        vault_password = result.stdout.strip()
        if not vault_password:
            raise click.ClickException(
                f'Found empty macOS Keychain entry for "{service_name}".'
            )
        return vault_password

    def write(self, key: str, value: str) -> None:
        service_name = keychain_service_name(key)
        try:
            subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-a",
                    os.environ.get("USER", ""),
                    "-s",
                    service_name,
                    "-w",
                    value,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise click.ClickException(
                "The macOS `security` tool is not available — the keychain "
                "vault backend only works on macOS."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise click.ClickException(
                f'Failed to store vault password in macOS Keychain for "{service_name}".'
            ) from exc


_BACKENDS: dict[str, type] = {
    KeychainBackend.name: KeychainBackend,
}


def get_backend(name: str = DEFAULT_VAULT_BACKEND) -> PasswordBackend:
    try:
        backend_cls = _BACKENDS[name]
    except KeyError as exc:
        available = ", ".join(sorted(_BACKENDS))
        raise click.ClickException(
            f'Unknown vault password backend "{name}". Available: {available}.'
        ) from exc
    return backend_cls()
