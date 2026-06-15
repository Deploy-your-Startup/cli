"""Safety guards around the bootstrap vault password.

These exist because a half-finished bootstrap used to leave the project vault
encrypted with the *public* ``TEMPLATE_VAULT_PASSWORD`` constant while a
different, freshly generated password was written to the Keychain / GitHub
secret. Nothing verified the end state, so the mismatch went unnoticed.

The helpers here let the wizard:
  * persist the vault password to the Keychain the moment it exists, and
  * assert after rotation that the vault decrypts with the *new* password and
    no longer with the template password — failing loudly if not.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from cli.vault.fields import contains_vault_blocks
from cli.vault.files import check_can_decrypt_with_password, is_full_vault_file


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


def verify_rotation(
    deployment_dir: Path, new_password: str, template_password: str
) -> None:
    """Assert the vault is now sealed with ``new_password`` only.

    Raises ``click.ClickException`` if any vault file still decrypts with the
    public template password, or if it cannot be decrypted with the new one.
    """
    files = iter_vault_files(deployment_dir)
    if not files:
        raise click.ClickException(
            f"Vault-Verifikation fehlgeschlagen: keine Vault-Dateien unter "
            f"{deployment_dir} gefunden."
        )

    not_rotated = [
        str(p.relative_to(deployment_dir))
        for p in files
        if not check_can_decrypt_with_password(p, new_password)
    ]
    if not_rotated:
        raise click.ClickException(
            "Vault-Verifikation fehlgeschlagen: lässt sich nach der Rotation "
            f"nicht mit dem neuen Passwort entschlüsseln: {', '.join(not_rotated)}"
        )

    still_template = [
        str(p.relative_to(deployment_dir))
        for p in files
        if check_can_decrypt_with_password(p, template_password)
    ]
    if still_template:
        raise click.ClickException(
            "Vault-Verifikation fehlgeschlagen: noch mit dem öffentlichen "
            "Template-Passwort entschlüsselbar (Rotation nicht durchgelaufen): "
            f"{', '.join(still_template)}"
        )


def _keychain_service_name(project_name: str) -> str:
    from cli.ansible_commands import keychain_service_name

    return keychain_service_name(project_name)


def store_keychain_password(project_name: str, vault_password: str) -> None:
    """Store (or update) the vault password in the macOS Keychain."""
    subprocess.run(
        [
            "security", "add-generic-password",
            "-a", os.environ.get("USER", ""),
            "-s", _keychain_service_name(project_name),
            "-w", vault_password,
            "-U",  # update if exists
        ],
        check=True,
        capture_output=True,
    )


def read_keychain_password(project_name: str) -> str | None:
    """Read the stored vault password, or ``None`` if absent/unavailable."""
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-a", os.environ.get("USER", ""),
                "-s", _keychain_service_name(project_name),
                "-w",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    pw = result.stdout.strip()
    return pw or None
