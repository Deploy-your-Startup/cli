"""
Ansible vault utilities for handling encrypted files and fields.
"""

from .common import verify_vault_password, generate_random_secret, create_vault_lib
from .files import (
    is_full_vault_file,
    rotate_full_vault_file,
    get_vault_file_content,
    update_vault_file,
    check_can_decrypt_with_password,
    safe_write,
)
from .fields import (
    extract_vault_block,
    replace_block,
    regen_vault_string,
    get_inline_vault_value,
    update_inline_vault_field,
    contains_vault_blocks,
    check_vault_blocks_with_password,
    rotate_inline_blocks,
)
from .utils import is_excluded, walk_files, find_vaulted_files

__all__ = [
    # Common utilities
    "verify_vault_password",
    "generate_random_secret",
    "create_vault_lib",
    # File operations
    "is_full_vault_file",
    "rotate_full_vault_file",
    "get_vault_file_content",
    "update_vault_file",
    "check_can_decrypt_with_password",
    "safe_write",
    # Field operations
    "extract_vault_block",
    "replace_block",
    "regen_vault_string",
    "get_inline_vault_value",
    "update_inline_vault_field",
    "contains_vault_blocks",
    "check_vault_blocks_with_password",
    "rotate_inline_blocks",
    # Repository utilities
    "is_excluded",
    "walk_files",
    "find_vaulted_files",
]
