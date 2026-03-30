"""Credential storage and management for Hetzner API tokens."""

import os
import stat
from datetime import datetime
from pathlib import Path

from . import config
from . import _output as ui


def save_token(
    token: str,
    project_name: str,
    token_name: str = "deploy-cli",
) -> Path:
    """Save API token to local config file. Returns the file path."""
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    env_content = (
        f"# Hetzner Cloud API Token\n"
        f"# Project: {project_name}\n"
        f"# Token name: {token_name}\n"
        f"# Generated: {datetime.now().isoformat()}\n"
        f"# {'=' * 40}\n"
        f"HETZNER_API_TOKEN={token}\n"
    )

    config.TOKEN_FILE.write_text(env_content)

    # Restrict file permissions (owner read/write only)
    try:
        os.chmod(config.TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        ui.warning("Could not set file permissions — please check manually.")

    ui.success(f"Token saved to: {config.TOKEN_FILE}")
    return config.TOKEN_FILE


def load_token() -> str | None:
    """Load existing token from config file."""
    if not config.TOKEN_FILE.exists():
        return None

    for line in config.TOKEN_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("HETZNER_API_TOKEN=") and not line.startswith("#"):
            return line.split("=", 1)[1]
    return None


def token_exists() -> bool:
    """Check if a token file already exists."""
    return config.TOKEN_FILE.exists() and load_token() is not None


def show_token_info():
    """Display info about stored token."""
    if not config.TOKEN_FILE.exists():
        ui.info("No stored token found.")
        return

    for line in config.TOKEN_FILE.read_text().splitlines():
        if line.startswith("#") and ":" in line:
            ui.info(line.lstrip("# "))

    token = load_token()
    if token:
        masked = token[:8] + "\u2022" * (len(token) - 12) + token[-4:]
        ui.info(f"Token: {masked}")
