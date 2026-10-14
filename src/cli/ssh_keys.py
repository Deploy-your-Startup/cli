"""Read public server-access keys without exposing private key contents."""

import subprocess
from pathlib import Path

import click


def read_public_key(path: Path) -> str:
    """Validate one OpenSSH public key using the installed OpenSSH executable."""
    try:
        content = path.expanduser().read_text().strip()
        if len(content.splitlines()) == 1 and content.startswith(
            ("ssh-", "ecdsa-", "sk-")
        ):
            result = subprocess.run(
                ["ssh-keygen", "-l", "-f", str(path.expanduser())],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return content
    except (OSError, UnicodeError):
        pass
    raise click.ClickException(
        "Use a readable OpenSSH public key file (.pub), not a private key."
    )


def public_key_is_valid(path: Path) -> bool:
    """Allow diagnostics to report an invalid/missing key without exposing it."""
    try:
        read_public_key(path)
    except click.ClickException:
        return False
    return True
