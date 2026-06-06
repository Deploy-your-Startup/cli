"""Browser-guided Cloudflare token creation for the startup CLI."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import click

from cli import wizard_output as ui

CF_SIGNUP_URL = "https://dash.cloudflare.com/sign-up"
CF_TOKEN_URL = "https://dash.cloudflare.com/profile/api-tokens"
CONFIG_DIR = Path.home() / ".config" / "cloudflare-bootstrap"
CHROME_USER_DATA_DIR = CONFIG_DIR / "chrome-profile"
CHROME_CHANNEL = "chrome"
DEFAULT_TIMEOUT = 60_000
LOGIN_WAIT_TIMEOUT = 300_000
DEFAULT_PERMISSIONS = [
    ("Account", "Cloudflare Pages", "Edit"),
    ("Account", "Account Settings", "Read"),
    ("User", "User Details", "Read"),
]


def _check_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _require_playwright() -> None:
    if not _check_playwright():
        raise click.ClickException(
            "Browser automation requires Playwright. Install with:\n"
            "  pip install 'deploy-your-startup-cli[browser]'\n"
            "  playwright install chromium"
        )


def create_api_token(
    *, token_name: str = "deploy-your-startup", headless: bool = False, register: bool = False
) -> str | None:
    """Launch a real browser, guide login/sign-up, and create a user API token."""
    _require_playwright()
    return asyncio.run(
        _async_create_api_token(
            token_name=token_name,
            headless=headless,
            register=register,
        )
    )


async def _async_create_api_token(
    *, token_name: str, headless: bool, register: bool
) -> str | None:
    async with CloudflareAutomation(headless=headless) as bot:
        ok = await bot.login(register=register)
        if not ok:
            ui.error("Cloudflare Login/Sign-up konnte nicht abgeschlossen werden.")
            return None

        return await bot.create_api_token(token_name=token_name)


class CloudflareAutomation:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._context = None
        self._page = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_USER_DATA_DIR),
            channel=CHROME_CHANNEL,
            headless=self.headless,
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=[
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ],
        )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        self._page.set_default_timeout(DEFAULT_TIMEOUT)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self):
        assert self._page is not None, "Browser not started"
        return self._page

    async def login(self, *, register: bool) -> bool:
        target_url = CF_SIGNUP_URL if register else CF_TOKEN_URL
        action = "Sign-up" if register else "Login"
        ui.action_start(f"Cloudflare {action} im Browser öffnen...")
        await self.page.goto(target_url, wait_until="domcontentloaded")
        await self._dismiss_blocking_ui()

        if await self._on_token_page():
            ui.action_done("Cloudflare Session bereits vorhanden")
            return True

        if register:
            ui.info(
                "Bitte im Browser den Cloudflare Sign-up abschließen.\n"
                "  Empfohlen: 'Continue with GitHub' verwenden."
            )
        else:
            ui.info("Bitte im Browser bei Cloudflare einloggen.")

        ok = await self._wait_for_session_ready()
        if ok:
            ui.action_done("Cloudflare Session bereit")
        else:
            ui.action_fail("Cloudflare Session nicht erkannt")
        return ok

    async def create_api_token(self, *, token_name: str) -> str | None:
        ui.action_start("Cloudflare API-Token erstellen...")
        await self.page.goto(CF_TOKEN_URL, wait_until="domcontentloaded")
        await self._dismiss_blocking_ui()
        await self._click_create_token_entry()
        await self._dismiss_blocking_ui()
        await self.page.get_by_role("button", name="Get started").first.click()
        await self.page.get_by_role("textbox").first.fill(token_name)

        for index, spec in enumerate(DEFAULT_PERMISSIONS):
            if index > 0:
                await self.page.get_by_role("button", name="Add more").first.click()
            await self._set_permission_row(index, *spec)

        await self.page.get_by_test_id("api_tokens_summary_button").click()
        await self.page.get_by_role("button", name="Create Token").click()

        token = await self._extract_token()
        if token:
            ui.action_done("Cloudflare API-Token erstellt")
            return token

        ui.action_fail("Cloudflare API-Token konnte nicht automatisch gelesen werden")
        ui.info("Bitte den Token aus dem Browser kopieren und hier einfügen.")
        manual_token = ui.text_input("Cloudflare API Token", hide_input=True)
        return manual_token or None

    async def _click_create_token_entry(self) -> None:
        try:
            await self._dismiss_blocking_ui()
            await self.page.get_by_role("button", name="Create Token").click(timeout=5_000)
        except Exception:
            pass

        try:
            await self.page.get_by_role("button", name="Get started").first.wait_for(
                state="visible", timeout=5_000
            )
            return
        except Exception:
            await self.page.goto(f"{CF_TOKEN_URL}/create", wait_until="domcontentloaded")

    async def _set_permission_row(
        self, index: int, resource: str, permission: str, level: str
    ) -> None:
        await self.page.get_by_role("button", name="Resources").nth(index).click()
        await self.page.get_by_role("option", name=resource, exact=True).click()

        permission_input = self.page.get_by_role("textbox", name="Permissions").nth(index)
        await permission_input.click()
        await permission_input.fill(permission)
        await self.page.get_by_role("option", name=permission, exact=True).click()

        level_input = self.page.get_by_role("combobox", name="Permissions levels").nth(index)
        await self._open_permission_level_menu(level_input)
        if level == "Edit":
            await level_input.press("ArrowDown")
        await level_input.press("Enter")

    async def _open_permission_level_menu(self, level_input) -> None:
        """Open the visible React select wrapper, not the hidden input itself."""
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await level_input.is_enabled(timeout=250):
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)

        await level_input.evaluate(
            """
            (element) => {
              const wrapper = element.closest('[role="combobox"]')?.parentElement || element.parentElement;
              if (wrapper) {
                wrapper.scrollIntoView({ block: 'center', inline: 'nearest' });
                wrapper.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                wrapper.dispatchEvent(new MouseEvent('click', { bubbles: true }));
              }
            }
            """
        )
        await level_input.focus()

    async def _extract_token(self) -> str | None:
        await self.page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)

        token = await self._extract_success_token()
        if token:
            return token

        try:
            copy_button = self.page.get_by_role("button", name=re.compile(r"Copy|Kopieren"))
            if await copy_button.first.count():
                await copy_button.first.click(timeout=3_000)
                token = await self.page.evaluate("navigator.clipboard.readText()")
                token = token.strip()
                if self._looks_like_token(token):
                    return token
        except Exception:
            pass

        selectors = [
            '[data-testid*="token"] input',
            '[data-testid*="token"] code',
            '[data-testid*="token"] pre',
            'input[readonly]',
            'code',
            'pre',
        ]
        for selector in selectors:
            try:
                items = self.page.locator(selector)
                count = await items.count()
                for i in range(count):
                    item = items.nth(i)
                    try:
                        value = await item.input_value(timeout=1_000)
                    except Exception:
                        value = ""
                    if self._looks_like_token(value):
                        return value.strip()
                    try:
                        value = await item.inner_text(timeout=1_000)
                    except Exception:
                        value = ""
                    if self._looks_like_token(value):
                        return value.strip()

            except Exception:
                continue

        return None

    async def _extract_success_token(self) -> str | None:
        """Handle Cloudflare's success screen with the dashed token box."""
        try:
            token = await self.page.evaluate(
                """
                () => {
                  const heading = Array.from(document.querySelectorAll('h4'))
                    .find((el) => /API token was successfully created/i.test(el.textContent || ''));
                  if (!heading) return '';

                  const container = heading.parentElement;
                  if (!container) return '';

                  const direct = container.querySelector('.select-all');
                  const value = (direct?.textContent || '').trim();
                  if (value) return value;

                  const curlCode = Array.from(container.querySelectorAll('code'))
                    .map((el) => el.textContent || '')
                    .join('\n');
                  const match = curlCode.match(/Bearer\\s+([A-Za-z0-9]{30,120})/);
                  return match ? match[1] : '';
                }
                """
            )
            if self._looks_like_token(token):
                return token.strip()
        except Exception:
            pass

        try:
            token = await self.page.evaluate(
                """
                async () => {
                  const heading = Array.from(document.querySelectorAll('h4'))
                    .find((el) => /API token was successfully created/i.test(el.textContent || ''));
                  if (!heading) return '';

                  const container = heading.parentElement;
                  const button = container?.querySelector('.select-all + button');
                  if (!(button instanceof HTMLElement)) return '';

                  button.click();
                  await new Promise((resolve) => setTimeout(resolve, 200));
                  return (await navigator.clipboard.readText()).trim();
                }
                """
            )
            if self._looks_like_token(token):
                return token.strip()
        except Exception:
            pass

        return None

    async def _wait_for_session_ready(self) -> bool:
        deadline = asyncio.get_running_loop().time() + (LOGIN_WAIT_TIMEOUT / 1000)
        while asyncio.get_running_loop().time() < deadline:
            await self._dismiss_blocking_ui()
            if await self._on_token_page():
                return True

            url = self.page.url
            if (
                url.startswith("https://dash.cloudflare.com/")
                and "/login" not in url
                and "/sign-up" not in url
            ):
                try:
                    await self.page.goto(CF_TOKEN_URL, wait_until="domcontentloaded")
                except Exception:
                    pass
                if await self._on_token_page():
                    return True

            await asyncio.sleep(1)

        return False

    async def _dismiss_blocking_ui(self) -> None:
        """Close common overlays that block clicks in Cloudflare's dashboard."""
        actions = [
            lambda: self.page.get_by_role("button", name="Reject All"),
            lambda: self.page.get_by_role("button", name="Accept All Cookies"),
            lambda: self.page.get_by_role("button", name=re.compile(r"Close|Schließen")),
        ]

        for locator_factory in actions:
            try:
                locator = locator_factory().first
                if await locator.is_visible(timeout=500):
                    await locator.click(timeout=2_000)
                    await asyncio.sleep(0.2)
            except Exception:
                continue

    async def _on_token_page(self) -> bool:
        if "/profile/api-tokens" in self.page.url and "login" not in self.page.url:
            return True

        try:
            heading = self.page.get_by_role("heading", name="User API Tokens")
            return await heading.first.is_visible(timeout=2_000)
        except Exception:
            return False

    @staticmethod
    def _looks_like_token(value: str) -> bool:
        value = value.strip()
        return bool(re.fullmatch(r"[A-Za-z0-9]{30,120}", value))
