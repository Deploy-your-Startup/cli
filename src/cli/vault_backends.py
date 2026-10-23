"""Pluggable backends for storing and retrieving the Ansible vault password.

A backend abstracts *where* the vault password lives: the macOS Keychain
(default) or 1Password through its `op` CLI. Every `startup ansible` command
resolves its password through `get_backend().read(project)`, so the command
layer does not care which one is configured.

The backend is chosen per computer, highest first: `STARTUP_VAULT_BACKEND`,
then `[vault] backend` in the user config (`startup config set vault-backend`),
then the Keychain. To add a backend, implement `PasswordBackend` and register
it in `_BACKENDS`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Protocol, runtime_checkable

import click
from keyring.errors import KeyringError

from cli import user_config

DEFAULT_VAULT_BACKEND = "keychain"
ONE_PASSWORD_BACKEND = "1password"


class PasswordNotFound(click.ClickException):
    """The backend works but holds no password for the project."""


def _macos_keyring():
    if sys.platform != "darwin":
        raise click.ClickException(
            "Full-stack bootstrap currently requires macOS Keychain."
        )
    from keyring.backends.macOS import Keyring

    return Keyring()


def keychain_service_name(project_name: str) -> str:
    """The entry name of a project's vault password, in every backend."""
    return f"VAULT_PASSWORD_{project_name}".upper().replace("-", "_")


def configured_backend_name() -> str:
    return (
        os.environ.get("STARTUP_VAULT_BACKEND")
        or user_config.get("vault", "backend")
        or DEFAULT_VAULT_BACKEND
    )


@runtime_checkable
class PasswordBackend(Protocol):
    """A place the vault password can be read from and written to.

    `key` is the project name; backends derive their own storage key from it
    (e.g. the Keychain service name). `read` raises `PasswordNotFound` when the
    backend has no entry for the project. `synced` tells whether other
    computers see what `write` stores; `label` names the place in messages.
    """

    name: str
    label: str
    synced: bool

    def read(self, key: str) -> str: ...

    def write(self, key: str, value: str) -> None: ...


class KeychainBackend:
    """macOS Keychain via the `security` CLI."""

    name = DEFAULT_VAULT_BACKEND
    label = "the macOS Keychain"
    synced = False

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
            raise PasswordNotFound(
                "The macOS `security` tool is not available — the keychain "
                "vault backend only works on macOS. Pass --vault-password, or "
                "keep vault passwords in 1Password: "
                "startup config set vault-backend 1password --op-vault <vault>."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise PasswordNotFound(
                f'No vault password found in macOS Keychain for "{service_name}". '
                "Run the command in an interactive terminal and paste the "
                f"password from your password manager entry {service_name}, or "
                "pass it through STARTUP_VAULT_PASSWORD."
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
            # The native Security API keeps the password out of process argv.
            # Pin the macOS backend rather than honoring external backend config.
            _macos_keyring().set_password(
                service_name, os.environ.get("USER", ""), value
            )
        except click.ClickException:
            raise
        except (KeyringError, OSError):
            # Provider errors can contain credentials. Never render their text.
            raise click.ClickException(
                f'Failed to store vault password in macOS Keychain for "{service_name}".'
            ) from None


SIGN_IN_HINT = (
    "Unlock the 1Password app and turn on Settings → Developer → "
    "Integrate with 1Password CLI, then check `op whoami`."
)


class OnePasswordBackend:
    """1Password via the `op` CLI, one Password item per project.

    The item is titled like the Keychain entry (`VAULT_PASSWORD_<PROJECT>`) and
    lives in the configured 1Password vault. Values travel through stdin and
    stdout only: assignment arguments would expose them to `ps`.
    """

    name = ONE_PASSWORD_BACKEND
    synced = True

    def __init__(self, vault: str | None = None, account: str | None = None):
        vault = (
            vault
            or os.environ.get("STARTUP_OP_VAULT")
            or user_config.get("vault", "op_vault")
        )
        if not vault:
            raise click.ClickException(
                "No 1Password vault is configured for vault passwords. Run "
                "startup config set vault-backend 1password --op-vault <vault>."
            )
        self.vault = vault
        self.account = account or user_config.get("vault", "op_account")

    @property
    def label(self) -> str:
        return f'1Password (vault "{self.vault}")'

    def _op(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        command = ["op", *args]
        if self.account:
            command += ["--account", self.account]
        try:
            return subprocess.run(
                command, input=stdin, capture_output=True, text=True, check=False
            )
        except FileNotFoundError:
            raise click.ClickException(
                "The 1Password CLI `op` is not installed: brew install 1password-cli"
            ) from None

    def _failure(self, result: subprocess.CompletedProcess, item: str) -> Exception:
        # `op` error text can echo the request; it never reaches the output.
        error = (result.stderr or "").lower()
        if "isn't an item" in error or "no item" in error:
            return PasswordNotFound(
                f'No vault password "{item}" in {self.label}. Store it with '
                "startup secrets copy-password, or paste it when a command asks."
            )
        if "isn't a vault" in error or "no vault" in error:
            return click.ClickException(
                f'1Password has no vault "{self.vault}" in this account. Create '
                "it in the app or run startup config set vault-backend 1password "
                "--op-vault <vault>."
            )
        return click.ClickException(
            f"1Password did not answer for {item}. {SIGN_IN_HINT}"
        )

    def check(self) -> None:
        """Raise unless `op` is signed in and can open the configured vault."""
        result = self._op("vault", "get", self.vault, "--format", "json")
        if result.returncode != 0:
            raise self._failure(result, f'the vault "{self.vault}"')

    def read(self, key: str) -> str:
        item = keychain_service_name(key)
        result = self._op("read", "--no-newline", f"op://{self.vault}/{item}/password")
        if result.returncode != 0:
            raise self._failure(result, item)
        if not result.stdout:
            raise click.ClickException(f'Found empty 1Password item "{item}".')
        return result.stdout

    def _item_ids(self, item: str) -> list[str]:
        listed = self._op("item", "list", "--vault", self.vault, "--format", "json")
        if listed.returncode != 0:
            raise self._failure(listed, item)
        return [
            entry["id"]
            for entry in json.loads(listed.stdout or "[]")
            if entry.get("title") == item
        ]

    def write(self, key: str, value: str) -> None:
        item = keychain_service_name(key)
        ids = self._item_ids(item)
        if len(ids) > 1:
            raise click.ClickException(
                f'{self.label} holds several items named "{item}". Keep one.'
            )
        password = {
            "id": "password",
            "type": "CONCEALED",
            "purpose": "PASSWORD",
            "label": "password",
            "value": value,
        }
        if not ids:
            template = {
                "title": item,
                "category": "PASSWORD",
                "fields": [
                    password,
                    {
                        "id": "notesPlain",
                        "type": "STRING",
                        "purpose": "NOTES",
                        "label": "notesPlain",
                        "value": f"Ansible Vault password of the {key} project, "
                        "read by the startup CLI.",
                    },
                ],
            }
            result = self._op(
                "item",
                "create",
                "--vault",
                self.vault,
                "-",
                stdin=json.dumps(template),
            )
        else:
            current = self._op(
                "item", "get", ids[0], "--vault", self.vault, "--format", "json"
            )
            if current.returncode != 0:
                raise self._failure(current, item)
            template = json.loads(current.stdout)
            fields = template.setdefault("fields", [])
            existing = next(
                (field for field in fields if field.get("id") == "password"), None
            )
            if existing is not None and existing.get("value") == value:
                return
            if existing is None:
                fields.append(password)
            else:
                existing["value"] = value
            result = self._op(
                "item",
                "edit",
                ids[0],
                "--vault",
                self.vault,
                stdin=json.dumps(template),
            )
        if result.returncode != 0:
            raise click.ClickException(
                f'Failed to store vault password in {self.label} as "{item}". '
                f"{SIGN_IN_HINT}"
            )


_BACKENDS: dict[str, type] = {
    KeychainBackend.name: KeychainBackend,
    OnePasswordBackend.name: OnePasswordBackend,
}


def get_backend(name: str | None = None) -> PasswordBackend:
    """The named backend, or the one configured for this computer."""
    name = name or configured_backend_name()
    try:
        backend_cls = _BACKENDS[name]
    except KeyError as exc:
        available = ", ".join(sorted(_BACKENDS))
        raise click.ClickException(
            f'Unknown vault password backend "{name}". Available: {available}.'
        ) from exc
    return backend_cls()
