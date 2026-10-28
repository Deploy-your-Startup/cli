"""Read-only headed smoke check. Does not log in or create provider resources."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from cli import cloudflare
from cli.browser_runtime import launch_options
from cli.hetzner import config
from cli.hetzner.automation import HetznerAutomation
from cli.hetzner.robot import HetznerKonsoleHAutomation


async def inspect_page(page, provider: str, url: str) -> None:
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    # Wait for a real rendered sign-in UI or an explicitly identified challenge.
    await page.locator(
        'input[type="email"], input[type="password"], input[name="username"], '
        'input[name="identifier"], input[name="email"], '
        'iframe[src*="challenges.cloudflare.com"], #challenge-running, '
        "#challenge-stage, #cf-challenge-running"
    ).first.wait_for(state="visible", timeout=30_000)
    result = {
        "provider": provider,
        "origin": f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}",
        "status": response.status if response else None,
        "login_fields": await page.locator(
            'input[type="email"]:visible, input[type="password"]:visible, '
            'input[name="username"]:visible, input[name="identifier"]:visible, '
            'input[name="email"]:visible'
        ).count(),
    }
    print(json.dumps(result), flush=True)
    assert result["status"] == 200 and result["login_fields"] > 0, provider


async def check_persistent_providers(root: Path) -> None:
    config.CHROME_USER_DATA_DIR = root / "hetzner" / "chrome-profile"
    cloudflare.CHROME_USER_DATA_DIR = root / "cloudflare" / "chrome-profile"
    async with HetznerAutomation(headless=False) as bot:
        await inspect_page(bot.page, "Hetzner Cloud", config.HETZNER_LOGIN_URL)
    async with HetznerKonsoleHAutomation(headless=False) as bot:
        await inspect_page(bot.page, "Hetzner KonsoleH", config.KONSOLEH_BASE_URL)
    async with cloudflare.CloudflareAutomation(headless=False) as bot:
        await inspect_page(bot.page, "Cloudflare", cloudflare.CF_TOKEN_URL)


def check_auth0() -> None:
    # Use the same synchronous, ephemeral launch as validate_live_login. A full
    # deployed-app check still needs the user's origin, tenant and manual login.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            **launch_options(playwright.chromium), headless=False
        )
        try:
            page = browser.new_page()
            response = page.goto(
                "https://manage.auth0.com/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            page.locator('input[type="email"], input[name="username"]').first.wait_for(
                state="visible", timeout=30_000
            )
            print(
                json.dumps(
                    {
                        "provider": "Auth0",
                        "origin": f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}",
                        "status": response.status if response else None,
                        "login_fields": page.locator(
                            'input[type="email"]:visible, input[name="username"]:visible'
                        ).count(),
                    }
                ),
                flush=True,
            )
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chrome", "chromium"), required=True)
    parser.add_argument("--profile-root", type=Path, required=True)
    args = parser.parse_args()
    os.environ["STARTUP_BROWSER"] = args.browser
    asyncio.run(check_persistent_providers(args.profile_root))
    check_auth0()


if __name__ == "__main__":
    main()
