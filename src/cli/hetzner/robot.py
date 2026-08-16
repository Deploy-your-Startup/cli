"""Playwright-based browser automation for Hetzner KonsoleH.

Handles: Login, Contact Handle creation, Domain registration/ordering.
KonsoleH (konsoleh.hetzner.com) is Hetzner's domain management interface,
separate from the Cloud Console but using the same Hetzner account.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..playwright_errors import playwright_error
from . import _output as ui
from . import config


class HetznerKonsoleHAutomation:
    """Manages browser automation for Hetzner KonsoleH (domain registration)."""

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

        Shares the same Chrome profile as HetznerAutomation so that
        login sessions carry over between Cloud Console and KonsoleH.
        Apple Passwords and other native extensions work automatically.
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
        # Always use a fresh tab so we do not inherit a stale Console page.
        self._page = await self._context.new_page()
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

    # ── Login ────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """Navigate to KonsoleH and wait for user to log in."""
        target_url = config.KONSOLEH_ORDER_URL

        # Check if already logged in
        try:
            await self.page.goto(target_url, wait_until="networkidle")
            if (
                "konsoleh.hetzner.com" in self.page.url
                and "accounts.hetzner" not in self.page.url
                and "/404" not in self.page.url
            ):
                ui.success("Already logged in to KonsoleH (saved session).")
                return True
        except playwright_error():
            pass

        ui.info("Opening Hetzner KonsoleH login...")
        await self.page.goto(target_url, wait_until="networkidle")

        ui.info(
            "Please log in via the browser:\n"
            "     1. Enter email and password\n"
            "     2. Complete 2FA if enabled\n"
            "     (Same account as Hetzner Cloud Console)"
        )

        try:
            await self.page.wait_for_url(
                f"{config.KONSOLEH_BASE_URL}/**",
                timeout=config.LOGIN_WAIT_TIMEOUT,
            )

            if "console.hetzner.com/404" in self.page.url or "/404" in self.page.url:
                ui.warning(
                    "Hetzner redirected to a 404 page after login. Opening KonsoleH again..."
                )
                await self.page.goto(target_url, wait_until="networkidle")

            return "konsoleh.hetzner.com" in self.page.url
        except playwright_error():
            return False

    # ── Handle Check/Creation ────────────────────────────────────────

    async def ensure_handles_exist(self) -> bool:
        """KonsoleH's old contact page is gone; continue and handle prompts later."""
        ui.info(
            "Skipping automatic contact handle check. "
            "If Hetzner asks for a handle later, continue manually in the browser."
        )
        return True

    # ── Domain Registration ──────────────────────────────────────────

    async def register_domain(
        self, domain: str, nameservers: list[str] | None = None
    ) -> bool:
        """
        Register a domain via KonsoleH order page.

        Opens the order page at konsoleh.hetzner.com/order.php,
        tries to pre-fill the domain name, and guides the user
        through the remaining steps.

        ``nameservers`` overrides the default Hetzner nameservers — pass the
        Cloudflare-assigned NS here to delegate DNS to Cloudflare directly at
        registration time. Falls back to manual instructions if automation fails.
        """
        nameservers = nameservers or config.HETZNER_NAMESERVERS
        ui.info(f'Registering domain "{domain}"...')

        if not await self._open_domain_registration_form():
            ui.warning(
                "Could not open KonsoleH's domain order form automatically. "
                "Please navigate to Neue Bestellung -> Domains -> Auswaehlen manually."
            )
            ui.ask("Press Enter to continue", default="")

        domain_name, tld = _split_domain(domain)

        try:
            await self._fill_domain_step_three(domain_name, tld, nameservers)

            ui.success(f"Domain name entered: {domain}")
        except playwright_error():
            ui.warning(f"Could not pre-fill domain — please enter '{domain}' manually.")

        ui.info(
            "Please complete the domain order in the browser:\n"
            "     1. Verify the domain name is correct\n"
            "     2. Select/create contact handles if needed\n"
            "     3. Verify nameservers:\n"
            f"        {', '.join(nameservers)}\n"
            "     4. Complete the order"
        )

        ui.info("Press Enter in the terminal once the order is submitted.")
        ui.ask("Press Enter to continue", default="")

        ui.success(
            f"Domain order for '{domain}' initiated.\n"
            "     Note: Domain will be reachable within 12-24 hours."
        )
        return True

    # ── Nameserver change (existing domain) ──────────────────────────

    async def set_nameservers(self, domain: str, nameservers: list[str]) -> bool:
        """Switch an already-registered KonsoleH domain to custom nameservers.

        Hetzner has no registrar API for NS delegation, so this drives KonsoleH's
        DNS management UI (verified flow):
          1. Read the domain's number from the product overview
          2. Open /domains/<number>/dns/update_nameservers
          3. Fill the ns1…ns5 fields and submit "Speichern"
        Falls back to a manual confirmation prompt if the form is not found.
        """
        ui.info(f'Switching nameservers for "{domain}" to:')
        for ns in nameservers:
            ui.info(f"     • {ns}")

        domain_number = await self._select_domain(domain)
        if not domain_number:
            ui.warning(f'Could not find "{domain}" in the KonsoleH overview.')

        if not await self._open_change_nameserver_form(domain_number):
            ui.warning(
                "Could not open the nameserver-change form automatically.\n"
                "     Please open: Einstellungen → DNS-Verwaltung → "
                "'Nameserver ändern' manually."
            )
            return await self._manual_nameserver_fallback(domain, nameservers)

        if not await self._fill_and_submit_nameservers(nameservers):
            return await self._manual_nameserver_fallback(domain, nameservers)

        ui.success(
            f"Nameserver change for '{domain}' submitted.\n"
            "     Note: DNS delegation can take up to 24 hours to propagate."
        )
        return True

    async def _select_domain(self, domain: str) -> str | None:
        """Select the domain on the product overview and return its number.

        KonsoleH addresses domains by an internal number (``D0123456789``) that
        every per-domain URL is built from. The overview links carry it as
        ``?domain_number=…``, so read it there instead of guessing a path.
        """
        try:
            await self.page.goto(
                f"{config.KONSOLEH_BASE_URL}/", wait_until="networkidle"
            )
            number = await self.page.evaluate(
                """(domain) => {
                    const link = [...document.querySelectorAll('a')].find(
                        a => (a.textContent || '').trim() === domain
                             && (a.getAttribute('href') || '').includes('domain_number=')
                    );
                    if (!link) return null;
                    const match = link.getAttribute('href').match(/domain_number=([^&]+)/);
                    return match ? match[1] : null;
                }""",
                domain,
            )
            link = self.page.locator(f'a:has-text("{domain}")').first
            if await link.count() > 0:
                await link.click(timeout=10000)
                await self.page.wait_for_load_state("networkidle")
            return number
        except playwright_error():
            return None

    async def _open_change_nameserver_form(self, domain_number: str | None) -> bool:
        """Open the nameserver form and confirm its fields are present."""
        if domain_number:
            try:
                await self.page.goto(
                    config.konsoleh_nameserver_url(domain_number),
                    wait_until="networkidle",
                )
                if await self.page.locator(config.SELECTORS_KONSOLEH_NS_FIELD).count():
                    return True
            except playwright_error():
                pass

        # Fallback: from DNS-Verwaltung, click through to the form.
        try:
            if domain_number:
                await self.page.goto(
                    config.konsoleh_dns_url(domain_number), wait_until="networkidle"
                )
            btn = self.page.locator(
                'a:has-text("Nameserver ändern"), button:has-text("Nameserver ändern")'
            ).first
            if await btn.count() > 0:
                await btn.click(timeout=5000)
                await self.page.wait_for_load_state("networkidle")
            return (
                await self.page.locator(config.SELECTORS_KONSOLEH_NS_FIELD).count() > 0
            )
        except playwright_error():
            return False

    async def _fill_and_submit_nameservers(self, nameservers: list[str]) -> bool:
        """Fill the nameserver fields and click Speichern. Returns success."""
        try:
            fields = self.page.locator(config.SELECTORS_KONSOLEH_NS_FIELD)
            count = await fields.count()
            if count == 0:
                return False

            for index in range(count):
                value = nameservers[index] if index < len(nameservers) else ""
                field = fields.nth(index)
                await field.click(timeout=5000)
                await field.fill("")
                if value:
                    await field.type(value, delay=30)

            await self.page.locator(
                'button:has-text("Speichern"), '
                'input[type="submit"][value*="Speichern" i]'
            ).first.click(timeout=5000)
            try:
                await self.page.wait_for_load_state("networkidle")
            except playwright_error():
                pass
            return True
        except playwright_error():
            return False

    async def _manual_nameserver_fallback(
        self, domain: str, nameservers: list[str]
    ) -> bool:
        """Ask the user to finish the change by hand, then confirm with Enter."""
        ui.info(
            "Please finish the nameserver change in the browser:\n"
            "     1. Einstellungen → DNS-Verwaltung → 'Nameserver ändern'\n"
            "     2. Enter exactly these nameservers:\n"
            f"        {', '.join(nameservers)}\n"
            "     3. Save (Speichern)"
        )
        ui.info("Press Enter in the terminal once the change is saved.")
        ui.ask("Press Enter to continue", default="")
        ui.success(
            f"Nameserver change for '{domain}' confirmed.\n"
            "     Note: DNS delegation can take up to 24 hours to propagate."
        )
        return True

    async def _open_domain_registration_form(self) -> bool:
        """Open KonsoleH's domain registration step via its own form submit."""
        if not await self._ensure_order_page():
            return False

        try:
            await self.page.evaluate("selectProduct('regonly', 32, 2)")
            try:
                await self.page.wait_for_load_state("networkidle")
            except playwright_error():
                pass
            try:
                await self.page.wait_for_selector(
                    "#domain_lookup_form, #search_domain, #tld_select, #next_button",
                    timeout=8000,
                )
                return True
            except playwright_error():
                pass
        except playwright_error():
            pass

        try:
            select_button = self.page.locator(
                '#domain a.btn-select[onclick*="regonly"], '
                'a.btn-select[onclick*="regonly"], '
                'a.btn-primary:has-text("Auswählen")'
            ).first
            await select_button.click(timeout=5000)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(
                "#domain_lookup_form, #search_domain, #tld_select, #next_button",
                timeout=8000,
            )
            return True
        except playwright_error():
            return False

    async def _ensure_order_page(self) -> bool:
        """Navigate back to KonsoleH's order page, even from account/product views."""
        candidates = [
            config.KONSOLEH_ORDER_URL,
            f"{config.KONSOLEH_ORDER_URL}#domain",
        ]

        for url in candidates:
            try:
                await self.page.goto(url, wait_until="networkidle")
            except playwright_error():
                continue

            if await self.page.locator("form#orderform").count() > 0:
                return True

            try:
                order_link = self.page.locator(
                    'a.top-bar-link[href="/order.php"], a[href="/order.php"]'
                ).first
                if await order_link.count() > 0:
                    await order_link.click(timeout=5000)
                    await self.page.wait_for_load_state("networkidle")
                    if await self.page.locator("form#orderform").count() > 0:
                        return True
            except playwright_error():
                pass

        return False

    async def _fill_domain_step_three(
        self, domain_name: str, tld: str, nameservers: list[str] | None = None
    ) -> None:
        """Fill KonsoleH's actual step-3 domain form and continue."""
        nameservers = nameservers or config.HETZNER_NAMESERVERS
        await self.page.wait_for_selector("#domain_lookup_form", timeout=8000)
        transfer_no = self.page.locator("#transfer_no").first
        await transfer_no.click(timeout=5000)

        domain_input = self.page.locator("#search_domain").first
        await domain_input.click(timeout=5000)
        await domain_input.fill("")
        await domain_input.type(domain_name, delay=60)
        await domain_input.dispatch_event("input")
        await domain_input.dispatch_event("change")

        tld_select = self.page.locator("#tld_select").first
        await tld_select.select_option(value=tld, timeout=5000)
        await tld_select.dispatch_event("change")

        nameserver_toggle = self.page.locator("#enable_nameserver").first
        await nameserver_toggle.click(timeout=5000)
        await self.page.wait_for_timeout(200)

        # Three NS fields exist; fill the provided ones (Cloudflare gives 2),
        # clear any leftover field so no stale Hetzner NS remains.
        for index, selector in enumerate(
            ("#nameserver1", "#nameserver2", "#nameserver3")
        ):
            value = nameservers[index] if index < len(nameservers) else ""
            field = self.page.locator(selector).first
            await field.click(timeout=5000)
            await field.fill("")
            if value:
                await field.type(value, delay=30)
            await field.dispatch_event("input")
            await field.dispatch_event("change")

        await self.page.evaluate(
            """
            () => {
                if (typeof splitDomainParts === 'function') splitDomainParts();
                if (typeof validateDomainName === 'function') validateDomainName();
                if (typeof toggleNextButton === 'function') toggleNextButton();
                if (typeof showErrors === 'function') showErrors();
            }
            """
        )

        await self.page.wait_for_function(
            """
            () => {
                const btn = document.querySelector('#next_button');
                const domain = document.querySelector('#search_domain');
                const tld = document.querySelector('#tld_select');
                return !!btn && !!domain && !!tld && domain.value.trim().length > 0 && tld.value.trim().length > 0 && !btn.disabled;
            }
            """,
            timeout=8000,
        )

        await self.page.locator("#next_button").first.click(timeout=5000)
        try:
            await self.page.wait_for_load_state("networkidle")
        except playwright_error():
            pass

    # ── Check Domain Availability ────────────────────────────────────

    async def check_domain_available(self, domain: str) -> bool | None:
        """
        Check if a domain is available for registration.
        Returns True if available, False if taken, None if check failed.
        """
        return None


def _split_domain(domain: str) -> tuple[str, str]:
    """Split a domain into the name part and TLD for Hetzner's order form."""
    host = urlparse(f"//{domain}").hostname or domain
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1]), f".{parts[-1]}"
    return host, ""
