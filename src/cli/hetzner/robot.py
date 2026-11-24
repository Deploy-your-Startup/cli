"""Playwright-based browser automation for Hetzner KonsoleH.

Handles: Login, Contact Handle creation, Domain registration/ordering.
KonsoleH (konsoleh.hetzner.com) is Hetzner's domain management interface,
separate from the Cloud Console but using the same Hetzner account.
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

import click

from ..browser_runtime import launch_persistent_context
from ..playwright_errors import playwright_error
from . import _output as ui
from . import config

ORDER_LINK = 'a[href="/orders"], a[href="/orders/"], a[href="/order.php"]'
DOMAIN_PRODUCT = '#domain a[href="/orders/domain_registration"]'
ORDER_PAGE = f"form#orderform, {DOMAIN_PRODUCT}"
DOMAIN_FORM = "form#domain_lookup_form"
DOMAIN_INPUT = "#domain_name_without_tld, #search_domain"


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
        """Share the selected browser's profile with Hetzner Cloud."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        self._context = await launch_persistent_context(
            self._playwright,
            config.CHROME_USER_DATA_DIR,
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

        # Signed out, the order page leads to Hetzner's sign-in, whose browser
        # check keeps navigating by script. Leave it alone until KonsoleH
        # is reached, then verify the account UI before declaring success.
        await self._open_konsoleh(target_url)
        if _konsoleh_account_url(self.page.url):
            await self._verify_account_page()
            ui.success("Already logged in to KonsoleH (saved session).")
            return True

        ui.info(
            "Please log in via the browser:\n"
            "     1. Enter email and password\n"
            "     2. Complete 2FA if enabled\n"
            "     (Same account as Hetzner Cloud Console)"
        )

        try:
            await self.page.wait_for_url(
                lambda url: _is_konsoleh(url) and "/auth.php" not in url,
                timeout=config.LOGIN_WAIT_TIMEOUT,
                wait_until="commit",
            )
        except playwright_error():
            return False

        if "/404" in self.page.url:
            ui.warning(
                "Hetzner redirected to a 404 page after login. Opening KonsoleH again..."
            )
            await self._open_konsoleh(target_url)
        await self._verify_account_page()
        return True

    async def _open_konsoleh(self, url: str) -> None:
        """Load the document without waiting for unrelated background requests."""
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded")
        except playwright_error():
            # Authentication can cause another navigation while this one loads.
            return
        if response and _is_konsoleh(response.url) and response.status >= 400:
            raise click.ClickException(
                f"KonsoleH returned HTTP {response.status} when opening the order page."
            )

    async def _verify_account_page(self) -> None:
        if not await self._wait_for_control(
            f"{ORDER_PAGE}, {ORDER_LINK}, {DOMAIN_FORM}"
        ):
            raise click.ClickException(
                "KonsoleH did not show the order navigation after sign-in."
            )
        if not _konsoleh_account_url(self.page.url):
            raise click.ClickException(
                "KonsoleH did not confirm the signed-in session."
            )

    async def _wait_for_control(self, selector: str, timeout: int = 5000) -> bool:
        try:
            await self.page.locator(selector).first.wait_for(
                state="attached", timeout=timeout
            )
            return True
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
        self,
        domain: str,
        nameservers: list[str] | None = None,
        contact_email: str | None = None,
        confirm_order: bool | None = None,
    ) -> bool:
        """
        Register a domain via KonsoleH order page.

        Opens the order page at konsoleh.hetzner.com/orders,
        tries to pre-fill the domain name, and guides the user
        through the remaining steps.

        ``nameservers`` overrides the default Hetzner nameservers — pass the
        Cloudflare-assigned NS here to delegate DNS to Cloudflare directly at
        registration time.

        The domain owner is an existing KonsoleH contact: the only one, the one
        whose e-mail matches ``contact_email``, or the one the user picks. New
        contacts are never created here. ``confirm_order`` True places the
        binding order without asking; False never clicks it; None asks in an
        interactive terminal, where declining ends the flow without an order.
        Otherwise the user orders in the browser and the flow waits until the
        summarised order is submitted with Hetzner's terms accepted.
        """
        nameservers = nameservers or config.HETZNER_NAMESERVERS
        ui.info(f'Registering domain "{domain}"...')

        await self._open_domain_registration_form()

        domain_name, tld = _split_domain(domain)

        try:
            await self._fill_domain_step_three(domain_name, tld, nameservers)

            ui.success(f"Domain name entered: {domain}")
        except playwright_error():
            raise click.ClickException(
                "KonsoleH opened the domain form but its fields could not be completed."
            ) from None

        if await self._choose_domain_owner(contact_email):
            await self._continue_to_order_summary()

        if not await self._wait_for_order_summary():
            ui.error(
                "Hetzner's order summary did not appear. No order was placed; "
                "finish or cancel it in the browser."
            )
            return False

        summary = await self._read_order_summary()
        if summary.get("domain", "").casefold() != domain.casefold() or not summary.get(
            "price"
        ):
            raise click.ClickException(
                "KonsoleH's order summary did not confirm the requested domain and price."
            )
        if contact_email and summary.get("email", "").strip().casefold() != (
            contact_email.strip().casefold()
        ):
            raise click.ClickException(
                "KonsoleH's order summary did not confirm the selected contact email."
            )
        ui.info(
            "Order summary from Hetzner:\n"
            f"     Domain: {summary.get('domain') or domain}\n"
            f"     Owner:  {summary.get('owner') or 'unknown'}"
            f" <{summary.get('email') or 'unknown'}>\n"
            f"     Price:  {summary.get('price') or 'unknown'}"
            f" ({summary.get('period') or 'billing period unknown'})"
        )

        if confirm_order is None and sys.stdin.isatty():
            confirm_order = ui.confirm(
                "Place this binding order now (accepts Hetzner's terms)?",
                default=False,
            )
            if not confirm_order:
                ui.info("No order was placed.")
                return False
        if confirm_order:
            return await self._place_order(domain)

        ui.info(
            "Review the order in the browser, accept Hetzner's terms and click "
            "'Zahlungspflichtig bestellen' yourself. Waiting for the order…"
        )
        if not await self._wait_for_order_placed(config.ORDER_WAIT_TIMEOUT):
            ui.error("No order was placed before the timeout.")
            return False
        ui.success(
            f"Domain order for '{domain}' placed.\n"
            "     Note: Registration usually completes within minutes."
        )
        return True

    async def _choose_domain_owner(self, contact_email: str | None) -> bool:
        """Select an existing contact as domain owner. Returns whether one was set."""
        owners = self.page.locator(config.SELECTORS_KONSOLEH_DOMAIN_OWNERS)
        try:
            await owners.first.wait_for(state="attached", timeout=10000)
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except playwright_error():
            ui.warning("Hetzner did not show the domain owner step.")
            return False

        contacts = await self._read_domain_contacts(owners.first)
        if not contacts:
            ui.warning(
                "No KonsoleH contact exists yet. Create the domain owner in the "
                "browser and continue the order there."
            )
            return False

        chosen = None
        if contact_email:
            matches = [
                contact
                for contact in contacts
                if contact["email"].lower() == contact_email.strip().lower()
            ]
            if not matches:
                known = ", ".join(contact["email"] for contact in contacts)
                raise click.ClickException(
                    f"No KonsoleH contact uses {contact_email}. Existing contacts: {known}."
                )
            chosen = matches[0]
        elif len(contacts) == 1:
            chosen = contacts[0]
        elif sys.stdin.isatty():
            for number, contact in enumerate(contacts, start=1):
                ui.info(f"{number}. {_describe_contact(contact)}")
            choice = click.prompt(
                "  Domain owner",
                type=click.IntRange(1, len(contacts)),
            )
            chosen = contacts[choice - 1]
        else:
            known = ", ".join(contact["email"] for contact in contacts)
            raise click.ClickException(
                f"Several KonsoleH contacts can own the domain ({known}); "
                "choose one with --contact-email."
            )

        await owners.first.select_option(index=chosen["index"])
        await self._wait_for_contact_email(chosen["email"])
        ui.success(f"Domain owner: {_describe_contact(chosen)}")
        return True

    async def _read_domain_contacts(self, select) -> list[dict]:
        original, options = await select.evaluate(_CONTACT_OPTIONS_JS)
        contacts = []
        try:
            for option in options:
                await select.select_option(index=option["index"], timeout=5000)
                await self._wait_for_contact_email()
                details = await self.page.evaluate(_CONTACT_FIELDS_JS)
                contacts.append({**option, **details})
        finally:
            if original >= 0:
                await select.select_option(index=original, timeout=5000)
        return contacts

    async def _wait_for_contact_email(self, email: str | None = None) -> None:
        try:
            await self.page.wait_for_function(
                """expected => {
                    const actual = document.getElementById('emails')?.value.trim();
                    return actual && (expected === null || actual === expected);
                }""",
                arg=email,
                timeout=5000,
            )
        except playwright_error():
            raise click.ClickException(
                "KonsoleH did not finish loading the selected contact."
            ) from None

    async def _continue_to_order_summary(self) -> None:
        try:
            await self.page.locator(config.SELECTORS_KONSOLEH_OWNER_NEXT).first.click(
                timeout=5000
            )
        except playwright_error():
            pass

    async def _wait_for_order_summary(self) -> bool:
        """Wait until the binding order form is shown, e.g. after manual steps."""
        try:
            await self.page.locator(
                config.SELECTORS_KONSOLEH_ORDER_FORM
            ).first.wait_for(state="attached", timeout=config.ORDER_WAIT_TIMEOUT)
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            return True
        except playwright_error():
            return False

    async def _read_order_summary(self) -> dict:
        try:
            return await self.page.evaluate(_READ_SUMMARY_JS)
        except playwright_error():
            return {}

    async def _place_order(self, domain: str) -> bool:
        """Accept the terms and submit the order form Hetzner just summarised."""
        order_form = await self._read_order_form()
        if not order_form:
            raise click.ClickException(
                "Could not verify the KonsoleH order form before submitting."
            )
        try:
            async with self._order_submission(order_form, 30_000) as submitted:
                await self.page.locator(
                    config.SELECTORS_KONSOLEH_ACCEPT_TERMS
                ).first.check(timeout=5000)
                await self.page.locator(
                    config.SELECTORS_KONSOLEH_PLACE_ORDER
                ).first.click(timeout=5000)
            placed = await _accepted(await submitted.value)
        except playwright_error():
            placed = False
        if not placed:
            ui.error(
                "Hetzner did not confirm the order. Check the browser and your "
                "KonsoleH orders before trying again."
            )
            return False
        ui.success(
            f"Domain order for '{domain}' placed.\n"
            "     Note: Registration usually completes within minutes."
        )
        return True

    async def _wait_for_order_placed(self, timeout: int) -> bool:
        """Wait until the user submits the summarised order with accepted terms.

        Only that submission counts: leaving or reloading the summary page
        removes the order form too, but orders nothing.
        """
        order_form = await self._read_order_form()
        try:
            async with self._order_submission(order_form, timeout) as submitted:
                pass
            return await _accepted(await submitted.value)
        except playwright_error():
            return False

    async def _read_order_form(self) -> dict | None:
        try:
            return await self.page.evaluate(
                _READ_ORDER_FORM_JS,
                [
                    config.SELECTORS_KONSOLEH_ORDER_FORM,
                    config.SELECTORS_KONSOLEH_ACCEPT_TERMS,
                ],
            )
        except playwright_error():
            return None

    def _order_submission(self, order_form: dict | None, timeout: int):
        return self.page.expect_request(
            lambda request: _submits_order(request, order_form), timeout=timeout
        )

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

    async def _open_domain_registration_form(self) -> None:
        """Open KonsoleH's domain registration step via its own form submit."""
        if await self.page.locator(DOMAIN_FORM).count():
            return
        await self._ensure_order_page()
        domain_product = self.page.locator(DOMAIN_PRODUCT).first
        if await domain_product.count():
            await self._select_domain_product(domain_product)
            return

        try:
            await self.page.evaluate("selectProduct('regonly', 32, 2)")
            if await self._wait_for_control(DOMAIN_FORM, timeout=8000):
                return
        except playwright_error():
            # The documented product link can still work without the legacy function.
            pass

        select_button = self.page.locator(
            '#domain a.btn-select[onclick*="regonly"], '
            'a.btn-select[onclick*="regonly"], '
            '#domain a.btn-primary:has-text("Auswählen")'
        ).first
        await self._select_domain_product(select_button)

    async def _select_domain_product(self, link) -> None:
        try:
            await link.click(timeout=5000)
        except playwright_error():
            raise click.ClickException(
                "KonsoleH showed the order page but the domain registration action could not be opened."
            ) from None
        if not await self._wait_for_control(DOMAIN_FORM, timeout=8000):
            raise click.ClickException(
                "KonsoleH did not show the domain lookup form after selecting domain registration."
            )

    async def _ensure_order_page(self) -> None:
        """Use the order form already loaded by login or follow the order link."""
        if await self.page.locator(ORDER_PAGE).count():
            return
        if await self.page.locator(ORDER_LINK).count():
            try:
                await self.page.locator(ORDER_LINK).first.click(timeout=5000)
            except playwright_error():
                raise click.ClickException(
                    "Could not open KonsoleH's order navigation."
                ) from None
        if not await self._wait_for_control(ORDER_PAGE):
            raise click.ClickException("KonsoleH did not show the product order form.")

    async def _fill_domain_step_three(
        self, domain_name: str, tld: str, nameservers: list[str] | None = None
    ) -> None:
        """Fill KonsoleH's actual step-3 domain form and continue."""
        nameservers = nameservers or config.HETZNER_NAMESERVERS
        await self.page.wait_for_selector("#domain_lookup_form", timeout=8000)
        transfer_no = self.page.locator("#transfer_no").first
        await transfer_no.click(timeout=5000)

        domain_input = self.page.locator(DOMAIN_INPUT).first
        await domain_input.click(timeout=5000)
        await domain_input.fill("")
        await domain_input.type(domain_name, delay=60)
        await domain_input.dispatch_event("input")
        await domain_input.dispatch_event("change")

        tld_select = self.page.locator("#tld_select").first
        await tld_select.select_option(value=tld, timeout=5000)
        await tld_select.dispatch_event("change")

        await self._fill_order_nameservers(nameservers)

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
                const domain = document.querySelector('#domain_name_without_tld, #search_domain');
                const tld = document.querySelector('#tld_select');
                return !!btn && !!domain && !!tld && domain.value.trim().length > 0 && tld.value.trim().length > 0 && !btn.disabled;
            }
            """,
            timeout=8000,
        )

        await self.page.locator("#next_button").first.click(timeout=5000)

    async def _fill_order_nameservers(self, nameservers: list[str]) -> None:
        modern = self.page.locator("#custom_nameservers")
        if await modern.count():
            if nameservers == config.HETZNER_NAMESERVERS:
                await modern.uncheck(timeout=5000)
                return
            await modern.check(timeout=5000)
        else:
            await self.page.locator("#enable_nameserver").first.check(timeout=5000)

        # Fill requested servers and clear the provider's remaining fields.
        fields = await self.page.locator(
            f"{DOMAIN_FORM} input[id^='nameserver']:visible"
        ).all()
        if len(fields) < len(nameservers):
            raise click.ClickException(
                "KonsoleH did not show enough nameserver fields."
            )
        for index, field in enumerate(fields):
            await field.fill(nameservers[index] if index < len(nameservers) else "")
            await field.dispatch_event("change")

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


def _is_konsoleh(url: str) -> bool:
    return urlparse(url).netloc == urlparse(config.KONSOLEH_BASE_URL).netloc


def _konsoleh_account_url(url: str) -> bool:
    return _is_konsoleh(url) and not any(part in url for part in ("/auth.php", "/404"))


def _describe_contact(contact: dict) -> str:
    name = " ".join(
        part for part in (contact["first_name"], contact["surname"]) if part
    )
    return f"{name or contact['label']} <{contact['email']}>, {contact['city']}"


# KonsoleH fills the owner form from a per-option script on "change"; reading the
# filled fields is the only way to tell contacts with the same name apart.
_READ_ORDER_FORM_JS = """([form, terms]) => {
  const order = document.querySelector(form);
  const accept = document.querySelector(terms);
  return order && accept ? {action: order.action, terms: accept.name} : null;
}"""


def _submits_order(request, order_form: dict | None) -> bool:
    """The summarised form, posted with Hetzner's terms accepted."""
    return bool(
        order_form
        and request.method == "POST"
        and request.url.split("#")[0] == order_form["action"]
        and f"{order_form['terms']}=" in (request.post_data or "")
    )


async def _accepted(request) -> bool:
    response = await request.response()
    return response is not None and response.ok


_CONTACT_OPTIONS_JS = """
select => [
    select.selectedIndex,
    [...select.options].flatMap((option, index) =>
        ['chooseContact', 'createNew'].includes(option.value)
            ? [] : [{index, label: option.text.trim()}])
]
"""

_CONTACT_FIELDS_JS = """
() => {
    const field = (id) => (document.getElementById(id) || {}).value || '';
    return {
        first_name: field('first_name'),
        surname: field('surname'),
        email: field('emails'),
        city: field('city'),
    };
}
"""

_READ_SUMMARY_JS = """
() => {
    const rows = {};
    for (const row of document.querySelectorAll('table.order-summary tr')) {
        const cells = row.querySelectorAll('td');
        if (cells.length === 2) rows[cells[0].innerText.trim()] = cells[1].innerText.trim();
    }
    const text = (selector) => (document.querySelector(selector) || {}).innerText || '';
    return {
        domain: rows['Domain:'] || '',
        owner: rows['Kontaktperson:'] || '',
        email: rows['E-Mailadresse:'] || '',
        price: text('table.order-summary .priceFull').trim(),
        period: text('table.order-summary .pricePeriod').trim(),
    };
}
"""
