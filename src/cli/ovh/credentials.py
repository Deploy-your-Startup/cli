"""Local storage for the OVH OpenStack clouds.yaml (compute credential).

Mirrors cli.hetzner.credentials but stores a clouds.yaml file instead of a
single token string. This is the local cache; the bootstrap also seals the same
content into the project vault as `openstack_clouds_<env>`.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cli.hetzner import _output as ui

from . import config


def save_clouds_yaml(clouds_yaml: str) -> Path:
    """Persist clouds.yaml to the local config dir (owner-only perms)."""
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.CLOUDS_FILE.write_text(clouds_yaml)
    try:
        os.chmod(config.CLOUDS_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        ui.warning("Could not set file permissions — please check manually.")
    ui.success(f"clouds.yaml saved to: {config.CLOUDS_FILE}")
    return config.CLOUDS_FILE


def load_clouds_yaml() -> str | None:
    """Load the cached clouds.yaml, or None if absent/empty."""
    if not config.CLOUDS_FILE.exists():
        return None
    content = config.CLOUDS_FILE.read_text()
    return content if content.strip() else None


def clouds_yaml_exists() -> bool:
    return config.CLOUDS_FILE.exists() and load_clouds_yaml() is not None


def delete_clouds_yaml() -> bool:
    if config.CLOUDS_FILE.exists():
        config.CLOUDS_FILE.unlink()
        return True
    return False
