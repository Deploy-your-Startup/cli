"""
Utilities for handling fully encrypted files in Ansible vault.
"""

import shutil
import logging

from .common import verify_vault_password, create_vault_lib
from ansible.parsing.vault import VaultEditor, VaultSecret

# Set up logger
logger = logging.getLogger(__name__)


def is_full_vault_file(file_path):
    """
    Check if a file is a fully encrypted Ansible vault file.

    Args:
        file_path (Path): Path to the file to check

    Returns:
        bool: True if the file is a fully encrypted vault file, False otherwise
    """
    try:
        with open(file_path, "r") as f:
            first_line = f.readline().strip()
            return first_line.startswith("$ANSIBLE_VAULT")
    except Exception:
        return False


def rotate_full_vault_file(
    file_path, old_password, new_password, dry_run=False, create_backup=True
):
    """
    Rotate the vault password for a fully encrypted file.

    Args:
        file_path (Path): Path to the file to rotate
        old_password (str): The old vault password
        new_password (str): The new vault password
        dry_run (bool): If True, only log what would be done without making changes
        create_backup (bool): If True, create a backup of the original file

    Returns:
        bool: True if the rotation was successful, False otherwise
    """
    try:
        if dry_run:
            logger.info(f"[DRY-RUN] Would rotate full vault file: {file_path}")
            return True

        # First, verify we can decrypt the file with the old password
        content = get_vault_file_content(file_path, old_password, strict=True)
        if content is None:
            logger.error(
                f"Error: Cannot decrypt {file_path} with the provided old password"
            )
            return False

        # Create a backup if requested
        if create_backup and file_path.exists():
            bak = file_path.with_suffix(file_path.suffix + ".bak")
            shutil.copy2(file_path, bak)
            logger.info(f"Backup saved: {bak}")

        # Create the vault libraries using the helper function
        old_vault_lib = create_vault_lib(old_password, strict=True)
        new_secret = VaultSecret(new_password.encode())

        # Use VaultEditor to rekey the file directly
        VaultEditor(old_vault_lib).rekey_file(str(file_path), new_secret)
        logger.info(f"Rotated vault file: {file_path}")

        return True
    except Exception as e:
        logger.error(f"Error rotating vault file {file_path}: {e}")
        return False


def get_vault_file_content(file_path, vault_password, strict=False):
    """
    Get the decrypted content of a fully encrypted vault file.

    Args:
        file_path (Path): Path to the file to decrypt
        vault_password (str): The vault password
        strict (bool): If True, only try the provided password, not the fallback password

    Returns:
        str or None: The decrypted content, or None if decryption fails
    """
    try:
        # Check if the file is a vault file
        if not is_full_vault_file(file_path):
            return None

        # Read the encrypted content
        with open(file_path, "r") as f:
            vault_text = f.read()

        # Verify the password can decrypt the content
        if verify_vault_password(vault_text, vault_password, strict=strict):
            # Create vault lib and decrypt content
            vault_lib = create_vault_lib(vault_password, strict=strict)
            decrypted = vault_lib.decrypt(vault_text.encode()).decode("utf-8")
            return decrypted.strip()
        return None
    except Exception as e:
        logger.error(f"Error getting vault file content: {e}")
        return None


def update_vault_file(file_path, new_content, vault_password):
    """
    Update a fully encrypted vault file with new content.

    Args:
        file_path (Path): Path to the file to update
        new_content (str): The new content to encrypt
        vault_password (str): The vault password

    Returns:
        bool: True if the update was successful, False otherwise
    """
    try:
        # Create a VaultLib using the helper function
        vault_lib = create_vault_lib(vault_password, strict=True)

        # Encrypt the content directly
        encrypted = vault_lib.encrypt(new_content).decode()

        # Write the encrypted content to the file
        with open(file_path, "w") as f:
            f.write(encrypted)

        logger.info(f"Updated vault file: {file_path}")
        return True
    except Exception as e:
        logger.error(f"Error updating vault file: {e}")
        return False


def check_can_decrypt_with_password(path, password):
    """
    Check if a file can be decrypted with the given password.

    Args:
        path (Path): Path to the file to check
        password (str): The vault password to check

    Returns:
        bool: True if the file can be decrypted, False otherwise
    """
    try:
        # If it's a full vault file, check using verify_vault_password
        if is_full_vault_file(path):
            with open(path, "r") as f:
                vault_text = f.read()
            return verify_vault_password(vault_text, password, strict=True)

        # For files with inline blocks, we'll need to defer to fields module
        from .fields import contains_vault_blocks, check_vault_blocks_with_password

        if contains_vault_blocks(path):
            return check_vault_blocks_with_password(path, password)
        return True
    except Exception as e:
        logger.error(f"Error checking if file can be decrypted: {e}")
        return False


def safe_write(path, content):
    """
    Atomically write content to a file with backup creation.

    Args:
        path (Path): Path to the file to write
        content (str): Content to write to the file

    Returns:
        bool: True if the write was successful, False otherwise
    """
    try:
        # Make a backup of the original file
        if path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            logger.info(f"Backup saved: {bak}")

        # Write to a temporary file first, then move atomically
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        logger.info(f"Updated: {path}")
        return True
    except Exception as e:
        logger.error(f"Error writing to {path}: {e}")
        return False
