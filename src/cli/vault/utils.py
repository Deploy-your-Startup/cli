"""Utilities for finding and navigating vault files in repositories."""

from __future__ import annotations

import logging
from pathlib import Path

# Set up logger
logger = logging.getLogger(__name__)

# Patterns for excluding files/directories
EXCLUDED_PATTERNS = [
    "*.pyc",
    "*.swp",
    "*.bak",
]

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".shared-roles",
    "node_modules",
    "site-packages",
}


def is_excluded(path: Path) -> bool:
    """
    Check if a path matches any excluded pattern.

    Args:
        path (Path): The path to check

    Returns:
        bool: True if the path matches an excluded pattern, False otherwise
    """
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return True

    for pat in EXCLUDED_PATTERNS:
        if path.match(pat):
            return True
    return False


def walk_files(base: Path, specific: Path | None = None):
    """
    Walk through files in a repository, yielding each file path.

    Args:
        base (Path): The base directory to walk
        specific (Path): A specific file or directory to limit the walk to

    Yields:
        Path: Each file path encountered during the walk
    """
    if specific:
        # If specific is a relative path, join it with the base path
        if not specific.is_absolute():
            potential_path = base / specific
            if potential_path.exists():
                specific = potential_path

        if specific.is_file() and not is_excluded(specific):
            yield specific
        elif specific.is_dir():
            for path in specific.rglob("*"):
                if path.is_file() and not is_excluded(path):
                    yield path
        return

    for path in base.rglob("*"):
        if path.is_file() and not is_excluded(path):
            yield path


def find_vaulted_files(repo=".", file_path=None, verbose=False):
    """
    Find all files containing vaulted content in a repository.

    Args:
        repo (str): Path to the repository
        file_path (str): Optional specific file or directory to search
        verbose (bool): Whether to enable verbose logging

    Returns:
        list: List of relative paths to files containing vaulted content
    """
    base_path = Path(repo)
    specific_path = Path(file_path) if file_path else None

    from .files import is_full_vault_file

    vaulted_files = []
    for path in walk_files(base_path, specific_path):
        try:
            if is_full_vault_file(path):
                rel_path = path.relative_to(base_path)
                if verbose:
                    logger.info(f"Found vault file: {rel_path}")
                vaulted_files.append(str(rel_path))
                continue

            text = path.read_text(encoding="utf-8", errors="ignore")
            if "$ANSIBLE_VAULT" in text or "!vault" in text:
                rel_path = path.relative_to(base_path)
                if verbose:
                    logger.info(f"Found file with vault blocks: {rel_path}")
                vaulted_files.append(str(rel_path))
        except Exception as e:
            logger.warning(f"Could not read {path}: {e}")

    return vaulted_files
