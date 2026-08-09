"""
Common utilities for Ansible vault operations.
"""

import secrets
import string
import sys

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
    # Diagnostics go to stderr, never stdout: `secrets get-field` exists to emit
    # one value, and a caller piping it must not have to filter chatter out.
    if not vault_text.startswith("$ANSIBLE_VAULT"):
        print(
            f"Text does not start with $ANSIBLE_VAULT: {vault_text[:20]}...",
            file=sys.stderr,
        )
        return False

    try:
        vault_secret = VaultSecret(vault_password.encode())
        vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
        vault.decrypt(vault_text.encode())
        return True
    # Ansible reports a wrong vault password as any of several exception
    # types (AnsibleError, ValueError, binascii.Error, UnicodeDecodeError, ...),
    # so this stays broad on purpose.
    except Exception as e:  # noqa: BLE001
        # The password itself never goes into the message. It used to, and with
        # the keychain fallback that is worse than it looks: a password the user
        # never typed would surface in terminals, scrollback and CI logs the
        # moment a file did not match.
        print(f"Failed to decrypt vault content: {e}", file=sys.stderr)
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
