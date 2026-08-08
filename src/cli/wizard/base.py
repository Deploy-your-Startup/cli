"""Wizard step base class + shared helpers used across multiple steps."""

from __future__ import annotations

import os
import subprocess
import webbrowser
from abc import ABC, abstractmethod
from pathlib import Path

from cli import wizard_output as ui

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


def prompt_user_public_key() -> str:
    """Ask the user for their own SSH public key for server SSH access."""
    candidates = [
        Path.home() / ".ssh" / "id_ed25519.pub",
        Path.home() / ".ssh" / "id_rsa.pub",
    ]
    default = next((str(p) for p in candidates if p.exists()), None)
    while True:
        path_str = ui.text_input(
            "Pfad zu deinem Public SSH Key (für SSH-Zugriff auf den Server)",
            default=default,
        )
        path = Path(path_str).expanduser()
        if not path.is_file():
            ui.error(f"Datei nicht gefunden: {path}")
            continue
        content = path.read_text().strip()
        if not content.startswith(("ssh-", "ecdsa-")):
            ui.error("Das sieht nicht nach einem OpenSSH Public Key aus.")
            continue
        return content


def open_browser(url: str, label: str) -> None:
    """Open a URL in the user's browser and report the outcome."""
    ui.action_start(f"{label} im Browser öffnen...")
    try:
        opened = webbrowser.open(url)
    except (webbrowser.Error, OSError):
        opened = False
    if opened:
        ui.action_done("Browser geöffnet")
    else:
        ui.action_fail("Browser konnte nicht automatisch geöffnet werden")
        ui.info(f"Bitte manuell öffnen: {url}")
