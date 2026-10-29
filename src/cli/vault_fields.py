"""Save provider values as inline vault fields; values only travel through stdin."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import click


def read_vault_field(target: Path, field: str) -> str | None:
    """Return a field's decrypted value, or None when it cannot be read."""
    result = subprocess.run(
        ["startup", "secrets", "get-field", "-f", str(target), "--field", field],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def store_vault_fields(
    project_dir: Path, target: Path, values: dict[str, str], provider: str
) -> None:
    """Write changed values with `startup secrets update`, after a dry run."""
    for field, value in values.items():
        if read_vault_field(target, field) == value:
            continue
        command = [
            "startup",
            "secrets",
            "update",
            "-r",
            str(target),
            "--field-stdin",
            field,
            "--create-in",
            str(target),
        ]
        for dry_run in (True, False):
            with tempfile.TemporaryDirectory(
                prefix="startup-vault-preview-"
            ) as preview:
                result = subprocess.run(
                    command + (["--dry-run"] if dry_run else []),
                    cwd=preview if dry_run else project_dir,
                    input=value,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            if result.returncode:
                raise click.ClickException(
                    f"Could not save {provider} configuration in the vault."
                )
