"""Playwright browser automation for OpenStack Horizon (OVH Public Cloud).

Mirrors cli.hetzner.automation: a persistent real-Chrome session, headed so the
user can complete manual login / 2FA. The goal is a clouds.yaml — Horizon offers
a one-click "Download clouds.yaml" after creating an Application Credential, so
the flow centers on capturing that download (robust to DOM changes) with the
user able to click manually if the best-effort automation misses.
"""

from __future__ import annotations

from pathlib import Path

from cli.hetzner import _output as ui

from . import config


class OVHAutomation:
    """Manages browser automation for OpenStack Horizon."""

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

    async def start(self):
        """Launch real Chrome with a persistent profile (session persists)."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        config.CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.CHROME_USER_DATA_DIR),
            channel=config.CHROME_CHANNEL,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            accept_downloads=True,
            args=config.chrome_launch_args(),
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
        self._page.set_default_timeout(config.DEFAULT_TIMEOUT)

    async def close(self):
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self):
        assert self._page is not None, "Browser not started"
        return self._page

    async def open_application_credentials(self) -> None:
        """Navigate to Horizon's Application Credentials page (tolerates login)."""
        try:
            await self.page.goto(
                config.HORIZON_APP_CREDENTIALS_URL, wait_until="domcontentloaded"
            )
        except Exception:
            ui.warning(
                "Could not open the Horizon URL directly. Navigate to "
                "Identity -> Application Credentials in the browser."
            )

    async def create_and_capture_clouds_yaml(self, name: str) -> str | None:
        """Best-effort create a credential, then capture the downloaded clouds.yaml.

        expect_download waits for ANY download to start within the timeout, so
        this works whether our automated clicks trigger it or the user creates
        the credential and clicks "Download clouds.yaml" by hand.
        """
        try:
            async with self.page.expect_download(
                timeout=config.DOWNLOAD_WAIT_TIMEOUT
            ) as download_info:
                await self._best_effort_create(name)
                ui.info(
                    'Create the credential if needed, then click '
                    '"Download clouds.yaml" — I will capture it automatically.'
                )
                download = await download_info.value
            tmp_path = await download.path()
            if tmp_path is None:
                return None
            return Path(tmp_path).read_text(encoding="utf-8")
        except Exception as exc:  # timeout or no download
            ui.warning(f"No clouds.yaml download was captured ({exc}).")
            return None

    async def _best_effort_create(self, name: str) -> None:
        """Try to drive the 'Create Application Credential' form; ignore misses."""
        try:
            create_btn = self.page.locator(config.SELECTORS_CREATE_BUTTON).first
            if await create_btn.count() and await create_btn.is_visible():
                await create_btn.click()
                name_input = self.page.locator(config.SELECTORS_NAME_INPUT).first
                if await name_input.count():
                    await name_input.fill(name)
                submit_btn = self.page.locator(config.SELECTORS_SUBMIT_BUTTON).first
                if await submit_btn.count() and await submit_btn.is_visible():
                    await submit_btn.click()
                download_link = self.page.locator(
                    config.SELECTORS_DOWNLOAD_CLOUDS_YAML
                ).first
                if await download_link.count() and await download_link.is_visible():
                    await download_link.click()
        except Exception:
            # Selectors are best-effort; the user completes the steps manually.
            pass
