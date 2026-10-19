"""Move the vault password between the Keychain and the user's password manager.

The Keychain entry is local to one Mac. The password manager is the copy that
travels to other computers, so the CLI asks for it in two places:

  * bootstrap, right after the password is generated: copy it to the clipboard
    and wait until the user has saved it under ``VAULT_PASSWORD_<PROJECT>``;
  * every command that needs it on a computer without the Keychain entry: ask
    for it once, check that it opens the vault, and store it in the Keychain.

The password itself is never printed and never placed in process arguments.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from cli import terminal_style as terminal
from cli.vault.fields import contains_vault_blocks
from cli.vault.files import check_can_decrypt_with_password, is_full_vault_file
from cli.vault_backends import PasswordBackend, keychain_service_name

RECOVERY_ATTEMPTS = 3


def iter_vault_files(deployment_dir: Path) -> list[Path]:
    """Return every YAML file under ``deployment_dir`` that holds vault content."""
    files: list[Path] = []
    if not deployment_dir.exists():
        return files
    for path in sorted(deployment_dir.rglob("*.y*ml")):
        try:
            if is_full_vault_file(path) or contains_vault_blocks(path):
                files.append(path)
        except OSError:
            continue
    return files


def vault_is_decryptable(deployment_dir: Path, password: str | None) -> bool:
    """True iff at least one vault file exists and *all* decrypt with ``password``.

    Requiring at least one file matters: an empty project would otherwise look
    "decryptable" and let a broken state pass as healthy.
    """
    if not password:
        return False
    files = iter_vault_files(deployment_dir)
    if not files:
        return False
    return all(check_can_decrypt_with_password(p, password) for p in files)


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _set_clipboard(value: str) -> bool:
    """Put ``value`` on the macOS clipboard through stdin; False if unavailable."""
    try:
        subprocess.run(
            ["pbcopy"], input=value, text=True, check=True, capture_output=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def hand_off_to_password_manager(project_name: str, vault_password: str) -> None:
    """Make sure a freshly generated vault password leaves this computer.

    Interactive runs copy the password to the clipboard and block until the
    user confirms it is saved. Non-interactive runs only explain where the
    password is and under which name it belongs in the password manager.
    """
    entry = keychain_service_name(project_name)
    click.echo()
    click.echo(
        terminal.style("  Save the vault password in your password manager", bold=True)
    )
    click.echo(f"    Name: {terminal.accent(entry)}")
    click.echo(
        "    Only this Mac's Keychain holds it. Other computers need it to "
        "deploy, and GitHub cannot show it again."
    )

    if not _interactive():
        click.echo(
            f'    Copy it from the Keychain entry "{entry}" into your password '
            "manager before you continue."
        )
        return

    copied = _set_clipboard(vault_password)
    if copied:
        click.echo("    The password is on your clipboard.")
    else:
        click.echo(
            f'    Copy it from the Keychain entry "{entry}" '
            "(Keychain Access → search for the name)."
        )
    try:
        while not click.confirm(f"  Saved it as {entry}?", default=False):
            click.echo(
                "    The project cannot be deployed from another computer without it."
            )
    finally:
        if copied:
            _set_clipboard("")


def _vault_dir(working_dir: Path) -> Path:
    if working_dir.name == "deployment":
        return working_dir
    deployment = working_dir / "deployment"
    return deployment if deployment.is_dir() else working_dir


def recover_from_password_manager(
    project_name: str, working_dir: Path, backend: PasswordBackend
) -> str | None:
    """Ask for a vault password missing from the Keychain and store it there.

    Returns ``None`` when nobody can answer (non-interactive runs), so the
    caller keeps its original error. A password is stored only after it opened
    the project's vault; without vault files to check it is used for this run
    only.
    """
    if not _interactive():
        return None

    entry = keychain_service_name(project_name)
    vault_dir = _vault_dir(working_dir)
    can_verify = bool(iter_vault_files(vault_dir))
    click.echo(
        f'  No vault password for "{project_name}" in this Mac\'s Keychain.', err=True
    )
    click.echo(f"  Paste it from your password manager ({entry}).", err=True)

    for _ in range(RECOVERY_ATTEMPTS):
        password = click.prompt(
            "  Vault password",
            hide_input=True,
            err=True,
            default="",
            show_default=False,
        ).strip()
        if not password:
            return None
        if not can_verify:
            click.echo(
                "  No vault files to check it against — using it for this run "
                "without saving it.",
                err=True,
            )
            return password
        if vault_is_decryptable(vault_dir, password):
            try:
                backend.write(project_name, password)
            except click.ClickException as error:
                # The password is verified; a missing Keychain only costs the
                # next run another prompt.
                click.echo(f"  {error.message} Using it for this run only.", err=True)
            else:
                click.echo(f"  Saved to the Keychain as {entry}.", err=True)
            return password
        click.echo("  That password does not open this project's vault.", err=True)
    return None
