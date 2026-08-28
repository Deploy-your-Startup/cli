"""Browser-guided Cloudflare token creation for the startup CLI."""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import click

from cli import wizard_output as ui
from cli.playwright_errors import playwright_error

CF_SIGNUP_URL = "https://dash.cloudflare.com/sign-up"
CF_TOKEN_URL = "https://dash.cloudflare.com/profile/api-tokens"
CONFIG_DIR = Path.home() / ".config" / "cloudflare-bootstrap"
CHROME_USER_DATA_DIR = CONFIG_DIR / "chrome-profile"
CHROME_CHANNEL = "chrome"
DEFAULT_TIMEOUT = 60_000
LOGIN_WAIT_TIMEOUT = 300_000
FORM_READY_TIMEOUT = 45_000
TOKEN_FORM_ATTEMPTS = 3
DEFAULT_PERMISSIONS = [
    ("Account", "Cloudflare Pages", "Edit"),
    ("Account", "Account Settings", "Read"),
    ("Zone", "Zone", "Edit"),  # grants account.zone.create (anlegen neuer Zonen)
    ("Zone", "DNS", "Edit"),  # DNS-Records in den (neuen) Zonen verwalten
    ("User", "User Details", "Read"),
]


def _check_playwright() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _require_playwright() -> None:
    if not _check_playwright():
        raise click.ClickException(
            "Browser automation requires Playwright. It ships with a normal\n"
            "CLI install but is pruned inside project deployments. Install it with:\n"
            "  uv pip install playwright   (or reinstall deploy-your-startup-cli)\n"
            "  playwright install chromium"
        )


def create_api_token(
    *,
    token_name: str = "deploy-your-startup",
    headless: bool = False,
    register: bool = False,
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

        # The re-auth below can fire at *any* point, so one pass through the
        # form is not enough — see `_wait_for_form_ready`. Each attempt starts
        # over from the token page, which is safe: nothing is created until the
        # final "Create Token" click, and a half-filled form is discarded by the
        # navigation that interrupted it.
        for attempt in range(1, TOKEN_FORM_ATTEMPTS + 1):
            try:
                await self._open_token_page()
                await self._submit_token_form(token_name)
                break
            except playwright_error() as exc:
                if attempt == TOKEN_FORM_ATTEMPTS:
                    ui.action_fail(
                        f"Token-Formular nach {TOKEN_FORM_ATTEMPTS} Versuchen "
                        "nicht abgeschlossen"
                    )
                    raise
                ui.info(
                    f"Cloudflare hat das Formular unterbrochen "
                    f"(Versuch {attempt}/{TOKEN_FORM_ATTEMPTS}) — "
                    f"Session prüfen und neu ausfüllen.\n"
                    f"  {type(exc).__name__}: {str(exc).splitlines()[0]}"
                )

        token = await self._extract_token()
        if token:
            ui.action_done("Cloudflare API-Token erstellt")
            return token

        ui.action_fail("Cloudflare API-Token konnte nicht automatisch gelesen werden")
        ui.info("Bitte den Token aus dem Browser kopieren und hier einfügen.")
        manual_token = ui.text_input("Cloudflare API Token", hide_input=True)
        return manual_token or None

    async def _open_token_page(self) -> None:
        """Land on the API-token page, completing a re-auth if one is demanded."""
        await self.page.goto(CF_TOKEN_URL, wait_until="domcontentloaded")
        await self._dismiss_blocking_ui()

        # Cloudflare re-authenticates its API-token pages even when a dashboard
        # session already exists, and that round trip through the SSO provider
        # lands on the dashboard root instead of the page that was requested.
        # `login()` may well have passed a moment earlier — it returns as soon as
        # the token page is reachable once. Without checking again here, every
        # click below hunts for a form that is on another page entirely, and the
        # run dies on a 60s timeout looking for "Get started".
        if not await self._on_token_page() and not await self._wait_for_session_ready():
            raise RuntimeError(
                "Cloudflare did not come back to the API token page after login."
            )

    async def _submit_token_form(self, token_name: str) -> None:
        await self._click_create_token_entry()
        await self._dismiss_blocking_ui()
        try:
            await self.page.get_by_role("button", name="Get started").first.click(
                timeout=15_000
            )
        except playwright_error():
            # "Get started" is the empty-state button; an account that already
            # has tokens opens the form directly.
            pass

        await self._wait_for_form_ready()
        await self.page.get_by_role("textbox").first.fill(token_name)

        for index, spec in enumerate(DEFAULT_PERMISSIONS):
            if index > 0:
                await self.page.get_by_role("button", name="Add more").first.click()
            await self._set_permission_row(index, *spec)

        await self.page.get_by_test_id("api_tokens_summary_button").click()
        await self.page.get_by_role("button", name="Create Token").click()

    async def _wait_for_form_ready(self) -> None:
        """Block until the permission form is really on screen.

        Cloudflare can bounce the tab back through its SSO provider *after* the
        form has already opened. A locator clicked while that redirect chain is
        in flight does not fail fast — it waits out the full timeout on a page
        that is navigating away, and the traceback then blames whichever field
        came next ("Resources") instead of the re-auth that actually happened.
        Waiting for the first row here turns that into one explicit, retryable
        failure before a single character is typed.
        """
        await self.page.get_by_role("button", name="Resources").first.wait_for(
            state="visible", timeout=FORM_READY_TIMEOUT
        )

    async def _click_create_token_entry(self) -> None:
        try:
            await self._dismiss_blocking_ui()
            await self.page.get_by_role("button", name="Create Token").click(
                timeout=5_000
            )
        except playwright_error():
            pass

        try:
            await self.page.get_by_role("button", name="Get started").first.wait_for(
                state="visible", timeout=5_000
            )
            return
        except playwright_error():
            await self.page.goto(
                f"{CF_TOKEN_URL}/create", wait_until="domcontentloaded"
            )

    async def _set_permission_row(
        self, index: int, resource: str, permission: str, level: str
    ) -> None:
        await self.page.get_by_role("button", name="Resources").nth(index).click()
        await self.page.get_by_role("option", name=resource, exact=True).click()

        permission_input = self.page.get_by_role("textbox", name="Permissions").nth(
            index
        )
        await permission_input.click()
        await permission_input.fill(permission)
        await self.page.get_by_role("option", name=permission, exact=True).click()

        level_input = self.page.get_by_role("combobox", name="Permissions levels").nth(
            index
        )
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
            except playwright_error():
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
            copy_button = self.page.get_by_role(
                "button", name=re.compile(r"Copy|Kopieren")
            )
            if await copy_button.first.count():
                await copy_button.first.click(timeout=3_000)
                # evaluate() yields None if the clipboard read resolves to
                # nothing (e.g. permission denied) — normalize before strip().
                token = await self.page.evaluate("navigator.clipboard.readText()")
                token = (token or "").strip()
                if self._looks_like_token(token):
                    return token
        except playwright_error():
            pass

        selectors = [
            '[data-testid*="token"] input',
            '[data-testid*="token"] code',
            '[data-testid*="token"] pre',
            "input[readonly]",
            "code",
            "pre",
        ]
        for selector in selectors:
            try:
                items = self.page.locator(selector)
                count = await items.count()
                for i in range(count):
                    item = items.nth(i)
                    try:
                        value = await item.input_value(timeout=1_000)
                    except playwright_error():
                        value = ""
                    if self._looks_like_token(value):
                        return value.strip()
                    try:
                        value = await item.inner_text(timeout=1_000)
                    except playwright_error():
                        value = ""
                    if self._looks_like_token(value):
                        return value.strip()

            except playwright_error():
                continue

        return None

    async def _extract_success_token(self) -> str | None:
        """Handle Cloudflare's success screen with the dashed token box."""
        try:
            token = await self.page.evaluate(
                """
                () => {
                  // The freshly created token is rendered in a `.select-all` box.
                  for (const el of document.querySelectorAll('.select-all')) {
                    const v = (el.textContent || '').trim();
                    if (/^[A-Za-z0-9_-]{30,120}$/.test(v)) return v;
                  }
                  // Fallback: parse the verify-curl snippet shown next to it.
                  const curlCode = Array.from(document.querySelectorAll('code,pre'))
                    .map((el) => el.textContent || '')
                    .join('\n');
                  const match = curlCode.match(/Bearer\\s+([A-Za-z0-9_-]{30,120})/);
                  return match ? match[1] : '';
                }
                """
            )
            if self._looks_like_token(token):
                return token.strip()
        except playwright_error():
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
        except playwright_error():
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
                except playwright_error():
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
            lambda: self.page.get_by_role(
                "button", name=re.compile(r"Close|Schließen")
            ),
        ]

        for locator_factory in actions:
            try:
                locator = locator_factory().first
                if await locator.is_visible(timeout=500):
                    await locator.click(timeout=2_000)
                    await asyncio.sleep(0.2)
            except playwright_error():
                continue

    async def _on_token_page(self) -> bool:
        if "/profile/api-tokens" in self.page.url and "login" not in self.page.url:
            return True

        try:
            heading = self.page.get_by_role("heading", name="User API Tokens")
            return await heading.first.is_visible(timeout=2_000)
        except playwright_error():
            return False

    @staticmethod
    def _looks_like_token(value: str) -> bool:
        value = value.strip()
        # Cloudflare tokens are alphanumeric and now carry a "cfut_" prefix
        # (underscore), so underscores/dashes must be allowed.
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{30,120}", value))
