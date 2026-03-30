"""Playwright-based browser automation for Hetzner Cloud Console.

Handles: Login, Project creation, API Token creation.
All UI interaction uses headed mode by default so the user can see
what happens and complete manual steps (login, 2FA, captcha).
"""

from __future__ import annotations

import asyncio

from . import config
from . import _output as ui


class HetznerAutomation:
    """Manages browser automation for Hetzner Cloud Console."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self):
        """Launch real Chrome with a persistent profile.

        Uses the system Chrome (not Playwright's bundled Chromium) so that
        native extensions like Apple Passwords work out of the box.
        Login sessions persist across runs via the user data directory.
        """
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        config.CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.CHROME_USER_DATA_DIR),
            channel=config.CHROME_CHANNEL,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
            args=config.chrome_launch_args(),
            # Keep extensions enabled so Apple Passwords works
            ignore_default_args=[
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ],
        )
        # Persistent context always has at least one page
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        self._page.set_default_timeout(config.DEFAULT_TIMEOUT)

    async def close(self):
        """Close browser. Session state is persisted automatically via user_data_dir."""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self):
        assert self._page is not None, "Browser not started"
        return self._page

    # ── Registration ────���────────────────────────────────────────────

    async def register_account(self, email: str) -> bool:
        """Open registration page — user completes manually."""
        ui.info("Opening Hetzner registration page...")
        await self.page.goto(config.HETZNER_REGISTER_URL, wait_until="networkidle")

        # Try to pre-fill email
        try:
            email_input = self.page.locator(
                'input[name="email"], input[type="email"]'
            ).first
            await email_input.fill(email, timeout=5000)
            ui.success(f"Email pre-filled: {email}")
        except Exception:
            ui.warning("Could not pre-fill email — please enter manually.")

        ui.info(
            "Please complete registration in the browser:\n"
            "     1. Set a password\n"
            "     2. Accept terms of service\n"
            "     3. Verify your email\n"
            "     4. Add payment information if prompted"
        )

        try:
            await self.page.wait_for_url(
                f"{config.HETZNER_BASE_URL}/**",
                timeout=config.LOGIN_WAIT_TIMEOUT,
            )
            return True
        except Exception:
            return False

    # ── Login ───────��────────────────────────────────────────────────

    async def login(self) -> bool:
        """Navigate to login and wait for user to complete (incl. 2FA)."""
        # Check if already logged in
        try:
            await self.page.goto(config.HETZNER_PROJECTS_URL, wait_until="networkidle")
            if "/projects" in self.page.url and "accounts.hetzner" not in self.page.url:
                ui.success("Already logged in (saved session).")
                return True
        except Exception:
            pass

        ui.info("Opening Hetzner login page...")
        await self.page.goto(config.HETZNER_LOGIN_URL, wait_until="networkidle")

        ui.info(
            "Please log in via the browser:\n"
            "     1. Enter email and password\n"
            "     2. Complete 2FA if enabled"
        )

        try:
            await self.page.wait_for_url(
                f"{config.HETZNER_BASE_URL}/**",
                timeout=config.LOGIN_WAIT_TIMEOUT,
            )
            return True
        except Exception:
            return False

    # ���─ Project Creation ────────────────��────────────────────────────

    async def create_project(self, project_name: str) -> bool:
        """Create a new project in Hetzner Cloud Console."""
        ui.info(f'Creating project "{project_name}"...')

        await self.page.goto(config.HETZNER_PROJECTS_URL, wait_until="networkidle")
        await asyncio.sleep(1)

        # Click "New Project" button
        try:
            new_project_btn = self.page.locator(
                config.SELECTORS_NEW_PROJECT_BUTTON
            ).first
            await new_project_btn.click(timeout=10000)
        except Exception:
            try:
                add_btn = self.page.locator(config.SELECTORS_ADD_BUTTON_FALLBACK).first
                await add_btn.click(timeout=5000)
            except Exception:
                ui.error("Could not find the 'New Project' button.")
                ui.warning("Please create the project manually in the browser.")
                return await self._wait_for_manual_project_creation(project_name)

        await asyncio.sleep(1)

        # Fill in project name
        try:
            name_input = self.page.locator(config.SELECTORS_PROJECT_NAME_INPUT).first
            await name_input.fill(project_name, timeout=5000)
        except Exception:
            ui.warning("Could not fill project name — please enter manually.")

        # Submit
        try:
            submit_btn = self.page.locator(config.SELECTORS_SUBMIT_BUTTON).first
            await submit_btn.click(timeout=5000)
        except Exception:
            ui.warning("Please confirm the dialog in the browser.")

        await asyncio.sleep(2)

        # Verify
        try:
            await self.page.wait_for_url("**/projects/**", timeout=15000)
            return True
        except Exception:
            if "/projects" in self.page.url:
                return True
            return False

    async def _wait_for_manual_project_creation(self, project_name: str) -> bool:
        """Fallback: wait for user to manually create the project."""
        ui.info(f'Please create a project named "{project_name}" manually.')
        try:
            await self.page.wait_for_url(
                "**/projects/**", timeout=config.LOGIN_WAIT_TIMEOUT
            )
            return True
        except Exception:
            return False

    # ── Token Creation ────────────���───────────────────────────��──────

    async def create_api_token(self, token_name: str = "deploy-cli") -> str | None:
        """Create an API token in the current project. Returns the token string."""
        ui.info(f'Creating API token "{token_name}"...')

        # Navigate to Security / API Tokens
        current_url = self.page.url
        if "/projects/" in current_url:
            base = current_url.split("/projects/")[0] + "/projects/"
            project_segment = current_url.split("/projects/")[1].split("/")[0]
            tokens_url = f"{base}{project_segment}/security/api-tokens"
        else:
            tokens_url = current_url.rstrip("/") + "/security/api-tokens"

        try:
            await self.page.goto(tokens_url, wait_until="networkidle")
        except Exception:
            # Fallback: navigate via sidebar
            try:
                security_link = self.page.locator(config.SELECTORS_SECURITY_LINK).first
                await security_link.click(timeout=5000)
                await asyncio.sleep(1)

                tokens_link = self.page.locator(config.SELECTORS_API_TOKENS_LINK).first
                await tokens_link.click(timeout=5000)
                await asyncio.sleep(1)
            except Exception:
                ui.warning("Could not navigate to the API tokens page automatically.")

        await asyncio.sleep(1)

        # Click "Generate API Token"
        try:
            gen_btn = self.page.locator(config.SELECTORS_GENERATE_TOKEN_BUTTON).first
            await gen_btn.click(timeout=10000)
        except Exception:
            ui.warning("Could not find 'Generate API Token' button.")
            ui.info("Please click 'Generate API Token' manually.")
            await asyncio.sleep(5)

        await asyncio.sleep(1)

        # Fill token description
        try:
            desc_input = self.page.locator(
                config.SELECTORS_TOKEN_DESCRIPTION_INPUT
            ).first
            await desc_input.fill(token_name, timeout=5000)
        except Exception:
            ui.warning("Could not fill token name — please enter manually.")

        # Select "Read & Write" permission
        try:
            rw_option = self.page.locator(config.SELECTORS_TOKEN_READWRITE).first
            await rw_option.click(timeout=5000)
        except Exception:
            ui.warning(
                "Could not set permission automatically — please select 'Read & Write'."
            )

        # Submit
        try:
            submit_btn = self.page.locator(config.SELECTORS_TOKEN_SUBMIT).first
            await submit_btn.click(timeout=5000)
        except Exception:
            ui.warning("Please confirm the dialog in the browser.")

        await asyncio.sleep(2)

        # Extract token
        token = await self._extract_token()

        if not token:
            ui.warning("Could not extract the token automatically.")
            ui.info("Please copy the token from the browser.")
            token = ui.ask("Paste API token here", password=True)

        return token if token else None

    async def _extract_token(self) -> str | None:
        """Try to extract the API token value from the page."""
        for selector in config.SELECTORS_TOKEN_VALUE:
            try:
                elements = self.page.locator(selector)
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    # Try inner text
                    try:
                        text = await el.inner_text(timeout=2000)
                        text = text.strip()
                        if self._looks_like_token(text):
                            return text
                    except Exception:
                        pass
                    # Try input value
                    try:
                        val = await el.get_attribute("value")
                        if val and self._looks_like_token(val):
                            return val.strip()
                    except Exception:
                        pass
            except Exception:
                continue

        # Fallback: try copy button + clipboard
        try:
            copy_btn = self.page.locator(config.SELECTORS_COPY_BUTTON).first
            await copy_btn.click(timeout=3000)
            token = await self.page.evaluate("navigator.clipboard.readText()")
            if token and self._looks_like_token(token.strip()):
                return token.strip()
        except Exception:
            pass

        return None

    @staticmethod
    def _looks_like_token(text: str) -> bool:
        """Check if a string looks like a Hetzner API token."""
        return len(text) > 30 and text.replace("-", "").replace("_", "").isalnum()
