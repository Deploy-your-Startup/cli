"""Wizard step base class + shared helpers used across multiple steps."""

from __future__ import annotations

import os
import subprocess
import webbrowser
from abc import ABC, abstractmethod
from pathlib import Path

import click

from cli import wizard_output as ui
from cli.ssh_keys import read_public_key

from .context import BootstrapContext


class WizardStep(ABC):
    """Base class for a bootstrap wizard step."""

    number: int
    name: str

    @abstractmethod
    def check(self, ctx: BootstrapContext) -> bool:
        """Return True if this step can be skipped (already done)."""

    @abstractmethod
    def run(self, ctx: BootstrapContext) -> None:
        """Execute the step. Raise on failure."""


# ── Shared helpers ───────────────────────────────────────────────────


def repo_exists(full_repo: str) -> bool:
    """Check if a GitHub repo exists via gh CLI."""
    try:
        subprocess.run(
            ["gh", "repo", "view", full_repo],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def has_placeholders(project_dir: Path) -> bool:
    """Check if the project still contains §§deploy_your_startup placeholders."""
    if not project_dir.exists():
        return False
    for root, _dirs, files in os.walk(project_dir):
        for f in files:
            fp = Path(root) / f
            try:
                if "§§deploy_your_startup" in fp.read_text(errors="ignore"):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
    return False


def is_pushed(project_dir: Path) -> bool:
    """Check if working tree is clean and the branch is in sync with origin."""
    try:
        result = subprocess.run(
            ["git", "status", "--branch", "--porcelain=v2"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        ahead_in_sync = False
        for line in result.stdout.splitlines():
            if line.startswith("# branch.ab"):
                parts = line.split()
                ahead = int(parts[2].lstrip("+"))
                ahead_in_sync = ahead == 0
            elif not line.startswith("#") and line.strip():
                return False
        return ahead_in_sync
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    return False


def prompt_user_public_key(
    *, non_interactive: bool = False, public_key_path: Path | None = None
) -> str:
    """Select and validate the operator key; never read or print private keys."""
    candidates = [
        Path.home() / ".ssh" / name for name in ("id_ed25519.pub", "id_rsa.pub")
    ]
    default = public_key_path or next((p for p in candidates if p.is_file()), None)
    ui.info(
        "This public SSH key grants you server access. It is separate from GitHub "
        "login; a dedicated deployment key is generated for CI. "
        "Use an existing .pub file, or create a key in another terminal with "
        "ssh-keygen -t ed25519."
    )
    while True:
        if public_key_path or non_interactive:
            if not default:
                raise click.ClickException(
                    "No SSH public key found. Use --ssh-public-key <path.pub> or "
                    "create one with ssh-keygen -t ed25519."
                )
            path = default.expanduser()
        else:
            path = Path(
                ui.text_input(
                    "Path to your public SSH key (for server access)",
                    default=str(default) if default else None,
                )
            ).expanduser()
        try:
            return read_public_key(path)
        except click.ClickException as error:
            if public_key_path or non_interactive:
                raise
            ui.error(error.format_message())


def open_browser(url: str, label: str) -> None:
    """Open a URL in the user's browser and report the outcome."""
    ui.action_start(f"{label}: opening in your browser...")
    try:
        opened = webbrowser.open(url)
    except (webbrowser.Error, OSError):
        opened = False
    if opened:
        ui.action_done("Browser opened")
    else:
        ui.action_fail("Could not open the browser automatically")
        ui.info(f"Open this link: {url}")
