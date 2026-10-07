"""Common utilities for Ansible Vault operations."""

import secrets
import string
import sys

from .process import VaultError, decrypt


def verify_vault_password(vault_text, vault_password, strict=False):
    """Verify through real Ansible without exposing input in diagnostics."""
    try:
        decrypt(vault_text, vault_password)
        return True
    except VaultError:
        print("Failed to decrypt vault content.", file=sys.stderr)
        return False


def generate_random_secret(length=32):
    """
    Generate a URL-safe random secret not starting with '-' or '_'.

    Args:
        length (int): The length of the secret to generate

    Returns:
        str: A random secret string
    """
    alphabet = string.ascii_letters + string.digits
    first = secrets.choice(string.ascii_letters)
    rest = "".join(secrets.choice(alphabet + "-_") for _ in range(length - 1))
    return first + rest
