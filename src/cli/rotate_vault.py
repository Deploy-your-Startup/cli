"""
Rotate Ansible vault passwords for files (full vault files or inline vault blocks).
Features:
- Atomic writes (no .tmp backups)
- Pathlib usage
- Structured logging with levels
- Dry-run mode
- Regex-based inline block handling (no ruamel.yaml)
- Subcommands: rotate (all or specific file), status
"""

import logging
import sys
from pathlib import Path
from ansible.parsing.vault import VaultSecret

# Import from our modular vault package
from cli.vault import (
    # File handling
    is_full_vault_file,
    rotate_full_vault_file,
    check_can_decrypt_with_password,
    safe_write,
    # Field handling
    rotate_inline_blocks,
    contains_vault_blocks,
    # Repository utilities
    walk_files,
    find_vaulted_files,
)

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,  # Output to stdout instead of stderr
    )


def list_status(repo=".", file_path=None, verbose=False):
    """List files with vaulted content"""
    setup_logging(verbose)
    vaulted_files = find_vaulted_files(repo, file_path, verbose)

    for rel_path in vaulted_files:
        print(rel_path)

    return vaulted_files


def rotate_vault_password(
    repo=".",
    old_password=None,
    new_password=None,
    file_path=None,
    dry_run=False,
    verbose=False,
    strict=False,
    jobs=1,
):
    """Rotate vault passwords in a repository"""
    setup_logging(verbose)

    if not old_password or not new_password:
        logger.error("Both old and new passwords are required")
        return False

    VaultSecret(old_password.encode())
    VaultSecret(new_password.encode())

    base_path = Path(repo)
    specific_path = Path(file_path) if file_path else None

    # First, check if all vault files can be decrypted with the old password
    if strict:
        logger.info(
            "Running in strict mode - checking if all vault files can be decrypted with the old password"
        )
        files_to_check = []
        for path in walk_files(base_path, specific_path):
            try:
                if is_full_vault_file(path) or contains_vault_blocks(path):
                    files_to_check.append(path)
            except Exception as e:
                logger.error(f"Error reading {path}: {e}")

        undecryptable_files = []
        for path in files_to_check:
            if not check_can_decrypt_with_password(path, old_password):
                rel_path = path.relative_to(base_path)
                undecryptable_files.append(str(rel_path))

        if undecryptable_files:
            error_msg = f"Cannot decrypt the following files with the provided old password: {', '.join(undecryptable_files)}"
            logger.error(error_msg)
            print(f"ERROR: {error_msg}")
            return False

    rotated_files = []
    for path in walk_files(base_path, specific_path):
        try:
            if is_full_vault_file(path):
                # Use the modular rotate_full_vault_file function
                success = rotate_full_vault_file(
                    path,
                    old_password,
                    new_password,
                    dry_run=dry_run,
                    create_backup=True,
                )
                if success:
                    rotated_files.append(str(path.relative_to(base_path)))
                    if not dry_run:
                        logger.info(f"Rotated vault file: {path}")
            else:
                # Check for inline vault blocks
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "$ANSIBLE_VAULT" in text or "!vault" in text:
                    # Use the modular rotate_inline_blocks function
                    new_text, modified = rotate_inline_blocks(
                        text, old_password, new_password, dry_run
                    )
                    if modified:
                        if dry_run:
                            logger.info(f"[DRY-RUN] Would update: {path}")
                        else:
                            # Use the modular safe_write function
                            success = safe_write(path, new_text)
                            if success:
                                logger.info(f"Updated inline vault blocks: {path}")
                        rotated_files.append(str(path.relative_to(base_path)))
        except Exception as e:
            logger.error(f"Error processing {path}: {e}")

    return rotated_files
