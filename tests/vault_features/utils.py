"""
Utility functions for vault feature tests.
"""

import random
import string
from pathlib import Path

from cli.ansible_bin import ansible_bin


def get_ansible_vault_cmd():
    """Run the ansible-vault of the environment running the tests.

    `uv run` would re-sync the CLI's virtualenv to its default dependencies,
    which deletes files that tests running in parallel are using.
    """
    return [ansible_bin("ansible-vault")]


def generate_random_string(length=16):
    """Generate a random string for testing"""
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(length)
    )


def get_dyscli_path():
    """Get the path to the startup.py script"""
    return Path(__file__).parents[2] / "src" / "cli" / "startup.py"
