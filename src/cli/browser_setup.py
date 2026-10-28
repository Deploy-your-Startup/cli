"""Read-only discovery of Playwright's version-matched Chromium installation."""

from __future__ import annotations

import importlib.util
import os
import shlex
import sys
from pathlib import Path


def chromium_install_command() -> str:
    """Use this CLI's interpreter so the browser matches its Playwright package."""
    return f"{shlex.quote(sys.executable)} -m playwright install chromium"


def playwright_install_hint() -> str:
    return (
        "Browser automation requires Playwright. Reinstall deploy-your-startup-cli "
        "to restore its runtime dependencies, or install Playwright into this runtime:\n"
        f"  uv pip install --python {shlex.quote(sys.executable)} playwright\n"
        f"Then install the browser:\n  {chromium_install_command()}"
    )


def chromium_check() -> tuple[bool, str]:
    """Ask the installed driver for its executable; never download or launch."""
    if importlib.util.find_spec("playwright") is None:
        return False, playwright_install_hint()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    ready = executable.is_file() and os.access(executable, os.X_OK)
    label = "Playwright Chromium for browser setup"
    if not ready:
        label += f" (run: {chromium_install_command()})"
    return ready, label
