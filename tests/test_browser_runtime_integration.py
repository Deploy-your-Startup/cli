"""Real browser lifecycle and persistence; provider accounts are not required."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import click
import pytest

from cli import cloudflare
from cli.auth0_commands import validate_live_login
from cli.browser_runtime import chrome_executable
from cli.hetzner import config
from cli.hetzner.automation import HetznerAutomation
from cli.hetzner.robot import HetznerKonsoleHAutomation
from cli.preflight import local_checks


@pytest.fixture
def without_chrome(monkeypatch):
    # Model absent system Chrome at the filesystem boundary, not in our selector.
    chrome = chrome_executable()
    is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path != chrome and is_file(path))


@pytest.mark.parametrize("choice", ["auto", "auto-no-chrome", "chromium", "chrome"])
def test_browser_selection_preserves_provider_sessions(monkeypatch, tmp_path, choice):
    # GIVEN separate disposable provider profiles and the real installed browsers.
    chrome = chrome_executable()
    if choice == "auto-no-chrome":
        is_file = Path.is_file
        monkeypatch.setattr(
            Path,
            "is_file",
            lambda path, hidden=chrome: path != hidden and is_file(path),
        )
        chrome = None
        choice = "auto"
    if choice == "chrome" and chrome is None:
        pytest.skip("Optional explicit Chrome scenario requires installed Chrome")
    monkeypatch.setenv("STARTUP_BROWSER", choice)
    hetzner_profile = tmp_path / "hetzner" / "chrome-profile"
    cf_profile = tmp_path / "cloudflare" / "chrome-profile"
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", hetzner_profile)
    monkeypatch.setattr(cloudflare, "CHROME_USER_DATA_DIR", cf_profile)

    async def scenario():
        # WHEN Cloud closes and KonsoleH reopens the same browser profile.
        async with HetznerAutomation(headless=True) as bot:
            await bot.page.context.add_cookies(
                [
                    {
                        "name": "session",
                        "value": "synthetic",
                        "domain": "example.invalid",
                        "path": "/",
                        "expires": 2000000000,
                    }
                ]
            )
        async with HetznerKonsoleHAutomation(headless=True) as bot:
            cookies = await bot.page.context.cookies("https://example.invalid")
            assert any(cookie["value"] == "synthetic" for cookie in cookies)
        async with cloudflare.CloudflareAutomation(headless=True) as bot:
            assert await bot.page.context.cookies("https://example.invalid") == []

    asyncio.run(scenario())
    # THEN sessions survive, providers remain isolated, and no other profile is used.
    name = "chrome-profile" if choice != "chromium" and chrome else "chromium-profile"
    for profile in (hetzner_profile, cf_profile):
        assert (profile.parent / name).is_dir()
        other = "chromium-profile" if name == "chrome-profile" else "chrome-profile"
        assert not (profile.parent / other).exists()


def test_invalid_selection_fails_before_profile_creation(monkeypatch, tmp_path):
    # GIVEN an invalid selection and no provider profile.
    monkeypatch.setenv("STARTUP_BROWSER", "firefox")
    profile = tmp_path / "chrome-profile"
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", profile)

    async def scenario():
        # WHEN the actual automation entrypoint starts.
        with pytest.raises(click.ClickException, match="STARTUP_BROWSER"):
            async with HetznerAutomation(headless=True):
                pytest.fail("Invalid browser must not launch")

    asyncio.run(scenario())
    assert not profile.exists()
    # The real synchronous Auth0 entrypoint uses the same policy without persisting state.
    with pytest.raises(click.ClickException, match="STARTUP_BROWSER"):
        validate_live_login("https://example.invalid", "tenant.example.invalid", 1)


def test_doctor_does_not_download_missing_chromium(monkeypatch, tmp_path):
    # GIVEN an empty browser cache and forced managed Chromium.
    cache = tmp_path / "browser-cache"
    monkeypatch.setenv("STARTUP_BROWSER", "chromium")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    # WHEN checking real local prerequisites without server-specific requirements.
    checks = local_checks(browser=True, server=False)
    # THEN the runtime is available but doctor has not downloaded anything.
    assert (
        True,
        "Managed Chromium for browser setup (downloaded on first use if missing)",
    ) in checks
    assert not cache.exists()


def test_explicit_missing_chrome_never_falls_back(
    monkeypatch, tmp_path, without_chrome
):
    # GIVEN a machine without Chrome and an explicit Chrome request.
    monkeypatch.setenv("STARTUP_BROWSER", "chrome")
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", tmp_path / "chrome-profile")

    async def scenario():
        # WHEN starting the public browser entrypoint, THEN Chromium is not used.
        with pytest.raises(click.ClickException, match="requires Google Chrome"):
            async with HetznerAutomation(headless=True):
                pytest.fail("Explicit Chrome must not fall back")

    asyncio.run(scenario())
    assert list(tmp_path.iterdir()) == []


def test_failed_download_uses_cli_runtime_and_withholds_output(monkeypatch, tmp_path):
    # GIVEN a missing browser and a failing installer process at the OS boundary.
    installer = tmp_path / "python"
    arguments = tmp_path / "arguments"
    installer.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{arguments}"\n'
        'echo "sensitive-installer-output" >&2\nexit 1\n'
    )
    installer.chmod(0o755)
    monkeypatch.setenv("STARTUP_BROWSER", "chromium")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(sys, "executable", str(installer))
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", tmp_path / "chrome-profile")

    async def scenario():
        # WHEN the actual browser entrypoint attempts installation.
        with pytest.raises(click.ClickException, match="Could not install") as error:
            async with HetznerAutomation(headless=True):
                pytest.fail("A failed download must not launch")
        # THEN repair instructions are safe and use this runtime, not global tooling.
        assert str(installer) in str(error.value)
        assert "sensitive-installer-output" not in str(error.value)

    asyncio.run(scenario())
    assert arguments.read_text().splitlines() == [
        "-m",
        "playwright",
        "install",
        "--no-shell",
        "chromium",
    ]
    assert not (tmp_path / "chromium-profile").exists()


@pytest.mark.skipif(
    os.environ.get("STARTUP_TEST_BROWSER_DOWNLOAD") != "1",
    reason="Set STARTUP_TEST_BROWSER_DOWNLOAD=1 for a real cache-empty download",
)
def test_first_use_downloads_matching_browser_then_reuses_cache(tmp_path):
    # GIVEN an empty isolated cache and no global playwright executable on PATH.
    cache = tmp_path / "browsers"
    environment = {
        **os.environ,
        "STARTUP_BROWSER": "chromium",
        "PLAYWRIGHT_BROWSERS_PATH": str(cache),
        "HETZNER_BOOTSTRAP_CONFIG": str(tmp_path / "hetzner"),
        "PATH": "/usr/bin:/bin",
    }
    scenario = """
import asyncio
import json
from pathlib import Path
import playwright
from cli.hetzner.automation import HetznerAutomation

async def main():
    async with HetznerAutomation(headless=True) as bot:
        await bot.page.goto('data:text/html,<h1>First use works</h1>')
        assert await bot.page.get_by_role('heading').inner_text() == 'First use works'
        installed = bot.page.context.browser.version
        registry = Path(playwright.__file__).parent / 'driver/package/browsers.json'
        expected = next(b['browserVersion'] for b in json.loads(registry.read_text())['browsers'] if b['name'] == 'chromium')
        assert installed == expected, (installed, expected)
        print(json.dumps({'version': installed}))

asyncio.run(main())
"""
    assert not cache.exists()
    # WHEN the actual CLI browser entrypoint starts for the first time, THEN it
    # installs and launches the exact version shipped in this runtime's registry.
    first = subprocess.run(
        [sys.executable, "-c", scenario],
        env=environment,
        capture_output=True,
        text=True,
        timeout=660,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert "Downloading Playwright-matched Chromium" in first.stderr
    assert json.loads(first.stdout)["version"]
    # WHEN repeated with the same cache, THEN no second download occurs.
    second = subprocess.run(
        [sys.executable, "-c", scenario],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert "Downloading" not in second.stderr
    assert json.loads(second.stdout) == json.loads(first.stdout)
