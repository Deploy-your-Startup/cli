"""Step 2 (pitch): obtain & validate a Cloudflare API token + account ID."""

from __future__ import annotations

import click
import httpx

from cli import wizard_output as ui
from cli.cloudflare import create_api_token
from cli.cloudflare_zones import ensure_zone

from ..base import WizardStep, open_browser
from ..context import BootstrapContext

CF_TOKEN_URL = "https://dash.cloudflare.com/profile/api-tokens"
CF_SIGNUP_URL = "https://dash.cloudflare.com/sign-up"


def guide_cloudflare_signup() -> None:
    """Guide the user through Cloudflare sign-up in the browser."""
    ui.info(
        "Set up your Cloudflare account in the browser.\n"
        "  You can choose 'Continue with GitHub'."
    )
    open_browser(CF_SIGNUP_URL, "Cloudflare Sign-up")
    ui.info(
        "Complete these steps in your browser:\n"
        "  1. Sign up or sign in\n"
        "  2. Verify your email if Cloudflare asks\n"
        "  3. Open the dashboard"
    )
    ui.text_input(
        "Press Enter when your Cloudflare account is ready",
        default="",
        show_default=False,
    )


def guide_cloudflare_token_creation() -> None:
    """Guide the user through Cloudflare token creation in the browser."""
    ui.info("Next, create your API token in the browser.")
    open_browser(CF_TOKEN_URL, "Cloudflare API token page")
    ui.info(
        "In your browser:\n"
        "  1. 'Create Token' → 'Create Custom Token'\n"
        "  2. Set permissions:\n"
        "       Account → Cloudflare Pages → Edit\n"
        "       Account → Account Settings → Read\n"
        "       Account → Zone → Edit\n"
        "       Zone    → DNS → Edit\n"
        "       User    → User Details → Read\n"
        "  3. 'Continue to summary' → 'Create Token'\n"
        "  4. Copy the token and paste it here"
    )


def validate_cf_token(token: str) -> tuple[bool, str | None]:
    """Verify a Cloudflare API token. Returns (ok, account_id_if_unique)."""
    try:
        r = httpx.get(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if not r.json().get("success"):
            return False, None
        r2 = httpx.get(
            "https://api.cloudflare.com/client/v4/accounts",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        accounts = r2.json().get("result", []) if r2.json().get("success") else []
        account_id = accounts[0]["id"] if len(accounts) == 1 else None
        return True, account_id
    except (httpx.HTTPError, ValueError, KeyError):
        return False, None


class CloudflareStep(WizardStep):
    number = 2
    name = "Cloudflare"

    def check(self, ctx: BootstrapContext) -> bool:
        return bool(
            ctx.cloudflare_api_token
            and ctx.cloudflare_account_id
            and ctx.cloudflare_nameservers
        )

    def run(self, ctx: BootstrapContext) -> None:
        # A token passed on the command line answers both questions below; the
        # account/zone handling afterwards stays exactly the same.
        if ctx.cloudflare_api_token:
            ui.action_start("Checking token...")
            ok, auto_account_id = validate_cf_token(ctx.cloudflare_api_token)
            if not ok:
                raise click.ClickException("The given Cloudflare token is not valid.")
            ui.action_done("Token verified")
            return self._resolve_account_and_zone(ctx, auto_account_id)

        has_account = (
            True
            if ctx.non_interactive
            else ui.confirm("Do you have a Cloudflare account?", default=True)
        )
        # Unattended, only the browser path gets by without further input.
        choice = (
            1
            if ctx.non_interactive
            else ui.numbered_choice(
                "How would you like to connect Cloudflare?",
                [
                    "Create a token in the browser (recommended)",
                    "Paste an existing token",
                ],
            )
        )

        while True:
            if choice == 1:
                token = create_api_token(
                    token_name=f"{ctx.project_name}-deploy",
                    register=not has_account,
                )
                if not token:
                    raise click.ClickException(
                        "Could not create a Cloudflare API token."
                    )
            else:
                if not has_account:
                    guide_cloudflare_signup()
                guide_cloudflare_token_creation()
                token = ui.text_input("Cloudflare API Token", hide_input=True)

            ui.action_start("Checking token...")
            ok, auto_account_id = validate_cf_token(token)
            if ok:
                ui.action_done("Token verified")
                ctx.cloudflare_api_token = token
                break
            ui.action_fail("Invalid token")
            if choice == 1:
                raise click.ClickException(
                    "The Cloudflare token created in the browser is invalid."
                )
            ui.error("Please try again.")

        return self._resolve_account_and_zone(ctx, auto_account_id)

    def _resolve_account_and_zone(
        self, ctx: BootstrapContext, auto_account_id: str | None
    ) -> None:
        """Pin down the account, then create or find the zone."""
        if auto_account_id:
            ctx.cloudflare_account_id = auto_account_id
            ui.info(f"Account ID detected: {auto_account_id}")
        elif ctx.non_interactive:
            raise click.ClickException(
                "Several Cloudflare accounts found and no way to ask which one "
                "to use. Pass --cloudflare-account-id."
            )
        else:
            ui.info(
                "Multiple accounts found — find your account ID in the "
                "sidebar at https://dash.cloudflare.com"
            )
            ctx.cloudflare_account_id = ui.text_input("Cloudflare account ID")

        ui.action_start(f"Cloudflare zone for {ctx.base_domain}: checking...")
        try:
            token, account_id = ctx.require_cloudflare()
            zone = ensure_zone(token, account_id, ctx.base_domain)
        except (RuntimeError, httpx.HTTPError) as exc:
            raise click.ClickException(
                f"Could not create Cloudflare zone: {exc}\n"
                "Does the token have 'Zone → Zone → Edit' and "
                "'Zone → DNS → Edit'?"
            ) from exc
        ctx.cloudflare_zone_id = zone.zone_id
        ctx.cloudflare_nameservers = zone.nameservers
        ctx.cloudflare_zone_is_subdomain = (
            bool(zone.name) and zone.name != ctx.base_domain
        )

        if ctx.cloudflare_zone_is_subdomain:
            ui.action_done(f"Existing root zone {zone.name} selected")
            ui.info(
                f"{ctx.base_domain} is a subdomain of {zone.name} — no "
                "nameserver changes needed; the subdomain will be added as a DNS record "
                "in the existing zone."
            )
            return

        if zone.created:
            ui.action_done("Cloudflare zone created")
        else:
            ui.action_done("Cloudflare zone already exists")
        ui.info(
            "Cloudflare nameservers for this domain:\n"
            + "\n".join(f"  • {ns}" for ns in zone.nameservers)
        )
