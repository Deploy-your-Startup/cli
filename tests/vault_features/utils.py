"""
Utility functions for vault feature tests.
"""

import subprocess
import random
import string
from pathlib import Path


def get_ansible_vault_cmd():
    """
    Determine how to run ansible-vault commands.
    Returns a list of command parts.
    """
    # Check if we should use uv run or direct command
    try:
        # Try running a simple uv command to check if it's available
        subprocess.run(["uv", "--version"], check=False, capture_output=True)
        return ["uv", "run", "ansible-vault"]
    except FileNotFoundError:
        # If uv is not found, use ansible-vault directly
        return ["ansible-vault"]


def generate_random_string(length=16):
    """Generate a random string for testing"""
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(length)
    )


def get_dyscli_path():
    """Get the path to the startup.py script"""
    return Path(__file__).parents[2] / "src" / "cli" / "startup.py"
