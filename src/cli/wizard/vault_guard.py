"""Safety guards around the bootstrap vault password.

These exist because a half-finished bootstrap used to leave the project vault
encrypted with the *public* ``TEMPLATE_VAULT_PASSWORD`` constant while a
different, freshly generated password was written to the password backend / GitHub
secret. Nothing verified the end state, so the mismatch went unnoticed.

The helpers here let the wizard:
  * persist the vault password to the password backend the moment it exists, and
  * assert after rotation that the vault decrypts with the *new* password and
    no longer with the template password — failing loudly if not.
"""

from __future__ import annotations

from pathlib import Path

import click

from cli.vault.files import check_can_decrypt_with_password
from cli.vault_backends import get_backend, keychain_service_name
from cli.vault_handoff import iter_vault_files, vault_is_decryptable

__all__ = [
    "iter_vault_files",
    "read_vault_password",
    "store_vault_password",
    "vault_is_decryptable",
    "vault_password_label",
    "verify_rotation",
]


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
            f"Vault verification failed: no vault files in {deployment_dir}."
        )

    not_rotated = [
        str(p.relative_to(deployment_dir))
        for p in files
        if not check_can_decrypt_with_password(p, new_password)
    ]
    if not_rotated:
        raise click.ClickException(
            "Vault verification failed: cannot decrypt after rotation "
            f"with the new password: {', '.join(not_rotated)}"
        )

    still_template = [
        str(p.relative_to(deployment_dir))
        for p in files
        if check_can_decrypt_with_password(p, template_password)
    ]
    if still_template:
        raise click.ClickException(
            "Vault verification failed: still decryptable with the public "
            "template password (rotation incomplete): "
            f"{', '.join(still_template)}"
        )


def store_vault_password(project_name: str, vault_password: str) -> None:
    """Store (or update) the vault password in the configured backend."""
    get_backend().write(project_name, vault_password)


def read_vault_password(project_name: str) -> str | None:
    """Read the stored vault password, or ``None`` if absent/unavailable."""
    try:
        return get_backend().read(project_name)
    except click.ClickException:
        return None


def vault_password_label(project_name: str) -> str:
    """Where the project's vault password is kept, for progress and summaries."""
    return f"{get_backend().label} as {keychain_service_name(project_name)}"
