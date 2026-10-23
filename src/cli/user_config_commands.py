"""`startup config`: choose where this computer keeps vault passwords."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import click

from cli import user_config
from cli import wizard_output as ui
from cli.vault_backends import (
    DEFAULT_VAULT_BACKEND,
    ONE_PASSWORD_BACKEND,
    OnePasswordBackend,
    configured_backend_name,
    get_backend,
)

DEFAULT_OP_VAULT = "Startups"


def _backend_source() -> str:
    if os.environ.get("STARTUP_VAULT_BACKEND"):
        return "STARTUP_VAULT_BACKEND"
    if user_config.get("vault", "backend"):
        return str(user_config.config_path())
    return "default"


def _save_one_password(vault: str, account: str | None) -> None:
    """Check that `op` reaches ``vault`` before the setting is saved."""
    OnePasswordBackend(vault=vault, account=account).check()
    user_config.update(
        "vault",
        {"backend": ONE_PASSWORD_BACKEND, "op_vault": vault, "op_account": account},
    )


@click.group("config")
def config():
    """Settings of this computer, shared by all projects."""


@config.command("show")
def show():
    """Show where vault passwords are kept and where that is configured."""
    name = configured_backend_name()
    click.echo(f"Config file:    {user_config.config_path()}")
    click.echo(f"Vault backend:  {name}  (from {_backend_source()})")
    if name == ONE_PASSWORD_BACKEND:
        vault = os.environ.get("STARTUP_OP_VAULT") or user_config.get(
            "vault", "op_vault"
        )
        click.echo(f"1Password vault:  {vault or '(not set)'}")
        if account := user_config.get("vault", "op_account"):
            click.echo(f"1Password account:  {account}")


@config.command("set")
@click.argument("setting", type=click.Choice(["vault-backend"]))
@click.argument(
    "value", type=click.Choice([DEFAULT_VAULT_BACKEND, ONE_PASSWORD_BACKEND])
)
@click.option(
    "--op-vault",
    help=f"1Password vault for the vault passwords (default: {DEFAULT_OP_VAULT}).",
)
@click.option(
    "--op-account",
    help="1Password account (sign-in address or ID), when several are signed in.",
)
def set_setting(setting, value, op_vault, op_account):
    """Change a setting of this computer.

    \b
      startup config set vault-backend 1password --op-vault Startups
      startup config set vault-backend keychain

    Vault passwords already in the Keychain stay there; move them with
    `startup secrets copy-password`.
    """
    if value == DEFAULT_VAULT_BACKEND:
        if op_vault or op_account:
            raise click.UsageError("--op-vault and --op-account need 1password.")
        path = user_config.update(
            "vault", {"backend": value, "op_vault": None, "op_account": None}
        )
        ui.success(f"Vault passwords are kept in the macOS Keychain ({path}).")
        return
    vault = op_vault or user_config.get("vault", "op_vault") or DEFAULT_OP_VAULT
    _save_one_password(vault, op_account)
    ui.success(
        f'Vault passwords are kept in 1Password, vault "{vault}" '
        f"({user_config.config_path()})."
    )
    click.echo(
        "  Move an existing project's password: startup secrets copy-password "
        "-r <project> --from keychain"
    )


def _one_password_signed_in() -> bool:
    if shutil.which("op") is None:
        return False
    result = subprocess.run(
        ["op", "account", "list", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return result.returncode == 0 and bool(json.loads(result.stdout or "[]"))
    except json.JSONDecodeError:
        return False


def offer_one_password() -> None:
    """Ask once, during bootstrap, whether 1Password should keep vault passwords.

    Only asked in a terminal, while nothing is configured and `op` knows an
    account. Either answer is saved, so the question never comes back.
    """
    if os.environ.get("STARTUP_VAULT_BACKEND") or user_config.get("vault", "backend"):
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    if not _one_password_signed_in():
        return
    click.echo()
    if not ui.confirm(
        "Keep vault passwords in 1Password, so every computer you sign in on "
        "can deploy?",
        default=True,
    ):
        user_config.update("vault", {"backend": DEFAULT_VAULT_BACKEND})
        ui.info("Vault passwords stay in the macOS Keychain.")
        return
    while True:
        vault = ui.text_input("1Password vault", default=DEFAULT_OP_VAULT).strip()
        try:
            _save_one_password(vault, None)
        except click.ClickException as error:
            ui.warning(error.message)
            if not ui.confirm("Try another vault?", default=True):
                user_config.update("vault", {"backend": DEFAULT_VAULT_BACKEND})
                ui.info("Vault passwords stay in the macOS Keychain.")
                return
            continue
        ui.success(f"Vault passwords go to {get_backend().label}.")
        return
