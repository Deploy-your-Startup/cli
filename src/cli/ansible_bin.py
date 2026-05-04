"""Resolve paths to ansible CLI binaries.

When the CLI is installed via `uv tool install`, only the `startup` entry
point is on PATH. The ansible binaries (ansible-vault, ansible-playbook,
...) live in the same venv as the running interpreter but are not exposed.
We locate them next to ``sys.executable`` and fall back to PATH lookup
for development installs.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def ansible_bin(name: str) -> str:
    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return str(candidate)
    return shutil.which(name) or name
