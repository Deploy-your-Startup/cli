"""Playwright-based browser automation for Hetzner Cloud Console.

Handles: Login, Project creation, API Token creation.
All UI interaction uses headed mode by default so the user can see
what happens and complete manual steps (login, 2FA, captcha).
"""

from __future__ import annotations

from ..playwright_errors import playwright_error
from . import _output as ui
from . import config


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
        """Close browser. Session state is persisted automatically via user_data_dir."""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def page(self):
        assert self._page is not None, "Browser not started"
        return self._page

    @staticmethod
    def _in_project(url: str) -> bool:
        """Return True if the URL points inside a specific project (has numeric ID)."""
        if "/projects/" not in url:
            return False
        segment = url.split("/projects/")[1].split("/")[0]
        return segment.isdigit()

    # ── Registration ────────────────────────────────────────────────────

    async def register_account(self, email: str) -> bool:
        """Open registration page — user completes manually."""
        ui.info("Opening Hetzner registration page...")
        await self.page.goto(config.HETZNER_REGISTER_URL, wait_until="domcontentloaded")

        try:
            email_input = self.page.locator(
                'input[name="email"], input[type="email"]'
            ).first
            await email_input.fill(email, timeout=5000)
            ui.success(f"Email pre-filled: {email}")
        except playwright_error():
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
        except playwright_error():
            return False

    # ── Login ───────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """Navigate to login and wait for user to complete (incl. 2FA)."""
        try:
            await self.page.goto(
                config.HETZNER_PROJECTS_URL, wait_until="domcontentloaded"
            )
            if "/projects" in self.page.url and "accounts.hetzner" not in self.page.url:
                ui.success("Already logged in (saved session).")
                return True
        except playwright_error():
            pass

        ui.info("Opening Hetzner login page...")
        await self.page.goto(config.HETZNER_LOGIN_URL, wait_until="domcontentloaded")

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
        except playwright_error():
            return False

    # ── Project Creation ────────────────────────────────────────────────

    async def create_project(self, project_name: str) -> bool:
        """Create or navigate to a project in Hetzner Cloud Console."""
        ui.info(f'Creating project "{project_name}"...')

        await self.page.goto(config.HETZNER_PROJECTS_URL, wait_until="domcontentloaded")

        # Fast path: project already exists → navigate into it
        try:
            existing = self.page.locator(f'[data-projectname="{project_name}"]').first
            if await existing.is_visible(timeout=2000):
                ui.success(
                    f'Project "{project_name}" already exists — navigating to it.'
                )
                await self._navigate_into_project(project_name)
                return self._in_project(self.page.url)
        except playwright_error():
            pass

        # Click "New Project"
        try:
            btn = self.page.locator(config.SELECTORS_NEW_PROJECT_BUTTON).first
            await btn.click(timeout=10000)
        except playwright_error():
            try:
                btn = self.page.locator(config.SELECTORS_ADD_BUTTON_FALLBACK).first
                await btn.click(timeout=5000)
            except playwright_error():
                ui.error("Could not find the 'New Project' button.")
                return await self._wait_for_manual_project_creation(project_name)

        # Fill name and submit
        try:
            name_input = self.page.locator(config.SELECTORS_PROJECT_NAME_INPUT).first
            await name_input.wait_for(state="visible", timeout=5000)
            await name_input.fill(project_name)
        except playwright_error():
            ui.warning("Could not fill project name — please enter manually.")

        try:
            submit_btn = self.page.locator(config.SELECTORS_SUBMIT_BUTTON).first
            await submit_btn.click(timeout=5000)
        except playwright_error():
            ui.warning("Please confirm the dialog in the browser.")

        # Hetzner does NOT redirect into the new project — it stays on the
        # projects list and just adds the card. (Waiting for a project URL here
        # used to time out for ~10s before falling through.) Wait for the card
        # to appear, then open it. This also covers the "name already taken"
        # case: the existing card is already there, so we just navigate into it.
        try:
            await self.page.locator(
                f'[data-projectname="{project_name}"]'
            ).first.wait_for(state="visible", timeout=10000)
        except playwright_error():
            pass

        if self._in_project(self.page.url):
            return True

        await self._navigate_into_project(project_name)
        return self._in_project(self.page.url)

    async def _navigate_into_project(self, project_name: str) -> None:
        """Navigate into the named project by finding its href on the projects list."""
        try:
            await self.page.goto(
                config.HETZNER_PROJECTS_URL, wait_until="domcontentloaded"
            )

            card_link = self.page.locator(
                f'a.project-card:has([data-projectname="{project_name}"])'
            ).first
            href = await card_link.get_attribute("href", timeout=5000)
            if href:
                url = (
                    href
                    if href.startswith("http")
                    else f"{config.HETZNER_BASE_URL}{href}"
                )
                await self.page.goto(url, wait_until="domcontentloaded")
                return

            # Fallback: click the span directly
            card = self.page.locator(f'[data-projectname="{project_name}"]').first
            await card.click(timeout=10000)
            await self.page.wait_for_url("**/projects/**", timeout=10000)
        except playwright_error():
            ui.warning(
                f'Could not navigate into project "{project_name}" automatically.'
            )

    async def _wait_for_manual_project_creation(self, project_name: str) -> bool:
        """Fallback: wait for user to manually create and open the project."""
        ui.info(
            f'Please create a project named "{project_name}" and open it in the browser.'
        )
        try:
            await self.page.wait_for_url(
                "**/projects/**", timeout=config.LOGIN_WAIT_TIMEOUT
            )
        except playwright_error():
            pass
        return self._in_project(self.page.url)

    # ── Token Creation ──────────────────────────────────────────────────

    async def create_api_token(self, token_name: str = "deploy-cli") -> str | None:
        """Create an API token in the current project. Returns the token string."""
        ui.info(f'Creating API token "{token_name}"...')

        # Navigate to the API tokens page by clicking the real links
        # (Sicherheit → API-Tokens). They carry the correct href, so this never
        # 404s. Direct URL is only a fallback (verified path: /security/tokens,
        # NOT /security/api-tokens which 404s).
        navigated = False
        try:
            security_link = self.page.locator(config.SELECTORS_SECURITY_LINK).first
            await security_link.click(timeout=5000)
            tokens_link = self.page.locator(config.SELECTORS_API_TOKENS_LINK).first
            await tokens_link.click(timeout=5000)
            navigated = True
        except playwright_error():
            pass

        if not navigated:
            current_url = self.page.url
            if "/projects/" in current_url:
                segment = current_url.split("/projects/")[1].split("/")[0]
                if segment.isdigit():
                    base = current_url.split("/projects")[0]
                    tokens_url = f"{base}/projects/{segment}/security/tokens"
                    try:
                        await self.page.goto(tokens_url, wait_until="domcontentloaded")
                    except playwright_error():
                        ui.warning("Could not navigate to the API tokens page.")

        # Click "Add API Token"
        try:
            gen_btn = self.page.locator(config.SELECTORS_GENERATE_TOKEN_BUTTON).first
            await gen_btn.click(timeout=10000)
        except playwright_error():
            ui.warning(
                "Could not find 'Add API Token' button — please click it manually."
            )

        # The dialog's <hc-dialog> wrapper is a zero-size element, so we do NOT
        # scope to it (that made wait_for(visible) hang). Instead we target the
        # dialog's fields directly by their unique data-test selectors — they are
        # distinct from anything on the page behind the dialog.

        # Fill description (waiting for the input itself is what confirms the
        # dialog is open).
        try:
            desc_input = self.page.locator(
                config.SELECTORS_TOKEN_DESCRIPTION_INPUT
            ).first
            await desc_input.wait_for(state="visible", timeout=10000)
            await desc_input.fill(token_name)
        except playwright_error():
            ui.warning("Could not fill token name — please enter manually.")

        # Select "Read & Write" (best effort — it is the default selection)
        try:
            rw_option = self.page.locator(config.SELECTORS_TOKEN_READWRITE).first
            await rw_option.click(timeout=3000)
        except playwright_error():
            pass

        # Submit and confirm the dialog actually advanced to the token view.
        if not await self._submit_token_modal():
            ui.warning(
                "Could not confirm the token dialog automatically — "
                "please click 'API-Token hinzufügen' in the browser."
            )

        # Reveal token (click "Klicken um anzuzeigen"). Short timeout: if it is
        # not there the token is likely already shown, no need to wait 10s.
        try:
            reveal = self.page.locator(
                '.click-to-show, :text("Klicken um anzuzeigen"), :text("Click to show")'
            ).first
            await reveal.click(timeout=3000)
        except playwright_error():
            pass

        token = await self._extract_token()

        if not token:
            ui.warning("Could not extract the token automatically.")
            ui.info("Please copy the token from the browser.")
            token = ui.ask("Paste API token here", password=True)

        return token if token else None

    async def _submit_token_modal(self) -> bool:
        """Click the dialog's accept button and verify the token view appeared.

        Returns True once the dialog advances (reveal/copy element shows up or the
        description field disappears). Retries the click once, since a single
        missed click was the original cause of the wizard stalling at the
        filled-but-unsubmitted dialog.
        """
        reveal_selector = (
            ".click-to-show, .click-to-copy__content, .click-to-copy__box, "
            'code, input[readonly], :text("Klicken um anzuzeigen"), '
            ':text("Click to show")'
        )

        for attempt in range(2):
            try:
                submit_btn = self.page.locator(config.SELECTORS_TOKEN_SUBMIT).first
                await submit_btn.wait_for(state="visible", timeout=5000)
                await submit_btn.click(timeout=5000)
            except playwright_error():
                if attempt == 0:
                    continue
                return False

            # Confirm we left the form: the token reveal/copy element shows up,
            # or the description input disappears (dialog advanced/closed).
            try:
                await self.page.locator(reveal_selector).first.wait_for(
                    state="visible", timeout=8000
                )
                return True
            except playwright_error():
                pass
            try:
                desc = self.page.locator(config.SELECTORS_TOKEN_DESCRIPTION_INPUT).first
                if not await desc.is_visible(timeout=1000):
                    return True
            except playwright_error():
                return True

        return False

    async def _extract_token(self) -> str | None:
        """Try to extract the API token value from the page."""
        for selector in config.SELECTORS_TOKEN_VALUE:
            try:
                elements = self.page.locator(selector)
                # Cap the scan: generic selectors like code/pre can match dozens
                # of elements, and each miss would otherwise wait out its timeout.
                count = min(await elements.count(), 20)
                for i in range(count):
                    el = elements.nth(i)
                    try:
                        text = await el.inner_text(timeout=500)
                        text = text.strip()
                        if self._looks_like_token(text):
                            return text
                    except playwright_error():
                        pass
                    try:
                        val = await el.get_attribute("value")
                        if val and self._looks_like_token(val):
                            return val.strip()
                    except playwright_error():
                        pass
            except playwright_error():
                continue

        # Fallback: try copy button + clipboard
        try:
            copy_btn = self.page.locator(config.SELECTORS_COPY_BUTTON).first
            await copy_btn.click(timeout=3000)
            token = await self.page.evaluate("navigator.clipboard.readText()")
            if token and self._looks_like_token(token.strip()):
                return token.strip()
        except playwright_error():
            pass

        return None

    @staticmethod
    def _looks_like_token(text: str) -> bool:
        """Check if a string looks like a Hetzner API token."""
        return len(text) > 30 and text.replace("-", "").replace("_", "").isalnum()
