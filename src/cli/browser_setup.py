"""Browser selection, profiles and read-only installation checks."""

from __future__ import annotations

import importlib.util
import os
import shlex
import sys
from pathlib import Path
from typing import TypedDict

import click


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


class BrowserLaunchOptions(TypedDict, total=False):
    channel: str
    ignore_default_args: list[str]


def browser_launch_options() -> BrowserLaunchOptions:
    """Opt in to system Chrome with extensions; managed Chromium is the default."""
    selection = os.environ.get("STARTUP_BROWSER", "chromium")
    if selection == "chromium":
        return {}
    if selection == "chrome":
        return {
            "channel": "chrome",
            "ignore_default_args": [
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ],
        }
    raise click.ClickException("STARTUP_BROWSER must be chromium or chrome.")


def browser_profile(chromium_profile: Path) -> Path:
    """Keep each engine's persistent state separate, preserving existing Chrome logins."""
    if browser_launch_options().get("channel") == "chrome":
        return chromium_profile.with_name("chrome-profile")
    return chromium_profile


def browser_check() -> tuple[bool, str]:
    if browser_launch_options().get("channel") != "chrome":
        return chromium_check()
    if importlib.util.find_spec("playwright") is None:
        return False, playwright_install_hint()
    paths = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return (
        any(path.is_file() and os.access(path, os.X_OK) for path in paths),
        "Google Chrome for browser setup (STARTUP_BROWSER=chrome; install Google Chrome)",
    )
