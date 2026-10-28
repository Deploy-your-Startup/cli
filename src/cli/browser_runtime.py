"""Shared browser policy; downloads use the installed CLI's Playwright version."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

import click


def browser_choice() -> str:
    choice = os.environ.get("STARTUP_BROWSER", "auto")
    if choice not in ("auto", "chrome", "chromium"):
        raise click.ClickException("STARTUP_BROWSER must be auto, chrome or chromium.")
    return choice


def chrome_executable() -> Path | None:
    paths = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path("/opt/google/chrome/chrome"),
    ]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if root := os.environ.get(variable):
            paths.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    return next((path for path in paths if path.is_file()), None)


def browser_check() -> tuple[bool, str]:
    """Read-only prerequisite check; never install or launch a browser."""
    try:
        choice = browser_choice()
    except click.ClickException as error:
        return False, error.message
    if importlib.util.find_spec("playwright") is None:
        return False, "Playwright (reinstall deploy-your-startup-cli)"
    if choice != "chromium" and chrome_executable():
        return True, "Google Chrome for browser setup"
    if choice == "chrome":
        return False, "Google Chrome required by STARTUP_BROWSER=chrome"
    return (
        True,
        "Managed Chromium for browser setup (downloaded on first use if missing)",
    )


def _install_chromium(executable: Path) -> None:
    if executable.is_file():
        return
    click.echo("Downloading Playwright-matched Chromium for browser setup...", err=True)
    command = [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=600)
        if result.returncode == 0 and executable.is_file():
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise click.ClickException(
        "Could not install managed Chromium. Check network access and cache permissions, "
        "then retry. To install manually with this CLI's runtime, run:\n  "
        + " ".join(shlex.quote(part) for part in command)
        + "\nAlternatively install Google Chrome and set STARTUP_BROWSER=chrome."
    )


def launch_options(chromium, profile: Path | None = None) -> dict:
    """Resolve once before launching; unrelated launch errors never cause fallback."""
    choice = browser_choice()
    chrome = chrome_executable() if choice != "chromium" else None
    if choice == "chrome" and chrome is None:
        raise click.ClickException(
            "STARTUP_BROWSER=chrome requires Google Chrome. Install Chrome for "
            "Apple Passwords, or use STARTUP_BROWSER=auto for managed Chromium."
        )
    if chrome is not None:
        options = {"executable_path": str(chrome)}
        selected_profile = profile
    else:
        executable = Path(chromium.executable_path)
        _install_chromium(executable)
        options = {"executable_path": str(executable)}
        selected_profile = profile.with_name("chromium-profile") if profile else None
    if selected_profile is not None:
        selected_profile.mkdir(parents=True, exist_ok=True)
        options["user_data_dir"] = str(selected_profile)
    return options


async def launch_persistent_context(playwright, profile: Path, **kwargs):
    """Stop the driver too if selection, download or launch fails."""
    try:
        options = await asyncio.to_thread(launch_options, playwright.chromium, profile)
        return await playwright.chromium.launch_persistent_context(**options, **kwargs)
    except BaseException:
        await playwright.stop()
        raise
