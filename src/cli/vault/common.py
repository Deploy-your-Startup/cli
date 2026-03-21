"""
Common utilities for Ansible vault operations.
"""

import secrets
import string
from ansible.parsing.vault import VaultLib, VaultSecret

DEFAULT_VAULT_IDENTITY = "default"


def verify_vault_password(vault_text, vault_password, strict=False):
    """
    Verify if the provided vault password can decrypt the vault text.

    Args:
        vault_text (str): The encrypted vault text
        vault_password (str): The vault password to verify
        strict (bool): If True, only try the provided password, not the fallback test password

    Returns:
        bool: True if the password can decrypt the vault text, False otherwise
    """
    if not vault_text.startswith("$ANSIBLE_VAULT"):
        print(f"Text does not start with $ANSIBLE_VAULT: {vault_text[:20]}...")
        return False

    try:
        vault_secret = VaultSecret(vault_password.encode())
        vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
        vault.decrypt(vault_text.encode())
        print("Successfully decrypted with password: xxxxx")
        return True
    except Exception as e:
        print(f"Failed to decrypt with password '{vault_password}': {e}")
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


def create_vault_lib(vault_password, strict=False):
    """
    Create a VaultLib instance with the given password.

    Args:
        vault_password (str): The vault password
        strict (bool): If True, only use the provided password, never the fallback test password

    Returns:
        VaultLib: A VaultLib instance
    """
    # Only use fallback in non-strict mode
    if not vault_password and not strict:
        vault_password = "test"  # Default for testing
    elif not vault_password and strict:
        raise ValueError("Vault password cannot be empty in strict mode")

    # Ensure the password is properly encoded
    if isinstance(vault_password, str):
        vault_password_bytes = vault_password.encode("utf-8")
    else:
        vault_password_bytes = vault_password

    # Create the vault lib
    vault_secret = VaultSecret(vault_password_bytes)
    return VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
