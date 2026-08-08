"""
Ansible vault utilities for handling encrypted files and fields.
"""

from .common import create_vault_lib, generate_random_secret, verify_vault_password
from .fields import (
    check_vault_blocks_with_password,
    contains_vault_blocks,
    extract_vault_block,
    get_inline_vault_value,
    regen_vault_string,
    replace_block,
    rotate_inline_blocks,
    update_inline_vault_field,
)
from .files import (
    check_can_decrypt_with_password,
    get_vault_file_content,
    is_full_vault_file,
    rotate_full_vault_file,
    safe_write,
    update_vault_file,
)
from .utils import find_vaulted_files, is_excluded, walk_files

__all__ = [
    "check_can_decrypt_with_password",
    "check_vault_blocks_with_password",
    "contains_vault_blocks",
    "create_vault_lib",
    # Field operations
    "extract_vault_block",
    "find_vaulted_files",
    "generate_random_secret",
    "get_inline_vault_value",
    "get_vault_file_content",
    # Repository utilities
    "is_excluded",
    # File operations
    "is_full_vault_file",
    "regen_vault_string",
    "replace_block",
    "rotate_full_vault_file",
    "rotate_inline_blocks",
    "safe_write",
    "update_inline_vault_field",
    "update_vault_file",
    # Common utilities
    "verify_vault_password",
    "walk_files",
]
