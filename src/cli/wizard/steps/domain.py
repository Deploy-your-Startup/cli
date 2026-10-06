"""Step 1: ensure the user owns the base domain (or guides them to buy it).

Fullstack: owning the domain is enough (DNS handled via the Hetzner DNS ansible
role). Pitch: DNS must be delegated to Cloudflare, so even an owned domain needs
its nameservers switched to the Cloudflare-assigned ones (collected in the
CloudflareStep, which runs first in the pitch flow).
"""

from __future__ import annotations

import click

from cli import wizard_output as ui

from ..base import WizardStep
from ..context import BootstrapContext


class DomainStep(WizardStep):
    number = 1
    name = "Domain"

    def check(self, ctx: BootstrapContext) -> bool:
        # Pitch always runs: run() decides buy vs. nameserver-switch.
        if ctx.kind == "pitch":
            return False
        if ctx.domain_owned is not None:
            return ctx.domain_owned
        if ctx.non_interactive:
            # Never buy a domain unattended — that spends money. Registering one
            # stays an explicit choice via --buy-domain.
            return True
        # Fullstack: ask; skip the buy flow if the user already owns it.
        choice = ui.numbered_choice(
            f'Do you already own "{ctx.base_domain}"?',
            [
                "Yes, I own this domain",
                "No, register it through Hetzner",
            ],
        )
        return choice == 1

    def run(self, ctx: BootstrapContext) -> None:
        if ctx.kind == "pitch":
            self._run_pitch(ctx)
        else:
            self._buy(ctx)

    # ── Pitch: delegate DNS to Cloudflare ────────────────────────────

    def _run_pitch(self, ctx: BootstrapContext) -> None:
        if ctx.cloudflare_zone_is_subdomain:
            ui.info(
                f'"{ctx.base_domain}" is a subdomain of a zone already delegated to '
                "Cloudflare — no domain registration or "
                "nameserver changes needed. DNS records will be created in the next "
                "step in the existing zone."
            )
            ui.action_done("Subdomain — no nameserver changes needed")
            return

        nameservers = ctx.cloudflare_nameservers
        if not nameservers:
            raise click.ClickException(
                "Cloudflare nameservers are missing — complete the Cloudflare step "
                "first."
            )

        if ctx.non_interactive:
            # Everything below either buys a domain or walks the user through a
            # registrar's UI — neither works without someone at the keyboard.
            raise click.ClickException(
                f'"{ctx.base_domain}" is not a subdomain of a zone already on '
                "Cloudflare, so its nameservers have to be switched at the "
                "registrar. Run the wizard interactively for this domain, or "
                "point it at Cloudflare first."
            )

        choice = ui.numbered_choice(
            f'Do you already own "{ctx.base_domain}"?',
            [
                "Yes, registered with Hetzner",
                "Yes, with another registrar",
                "No, register it through Hetzner",
            ],
        )

        if choice == 3:
            self._buy(ctx, nameservers=nameservers)
        elif choice == 1:
            self._switch_hetzner_nameservers(ctx, nameservers)
        else:
            self._manual_nameservers(ctx, nameservers)

    def _switch_hetzner_nameservers(
        self, ctx: BootstrapContext, nameservers: list[str]
    ) -> None:
        ui.info(
            "Opening KonsoleH to change the domain’s nameservers to Cloudflare. "
            "Sign in and save the change in your browser."
        )
        from cli.hetzner import set_domain_nameservers

        ok = set_domain_nameservers(domain=ctx.base_domain, nameservers=nameservers)
        if ok:
            ui.action_done("Nameservers changed to Cloudflare")
        else:
            ui.action_fail("Nameserver change failed")
            if not ui.confirm("Continue anyway?", default=False):
                raise click.ClickException("Cancelled.")

    def _manual_nameservers(
        self, ctx: BootstrapContext, nameservers: list[str]
    ) -> None:
        ns_lines = "\n".join(f"  • {ns}" for ns in nameservers)
        ui.info(
            "Set these nameservers at your domain registrar:\n"
            f"{ns_lines}\n"
            "Cloudflare will then manage DNS for this domain."
        )
        ui.text_input(
            "Press Enter after updating the nameservers",
            default="",
            show_default=False,
        )
        ui.action_done("Nameserver instructions acknowledged")

    # ── Buy a fresh domain at Hetzner ────────────────────────────────

    def _buy(self, ctx: BootstrapContext, nameservers: list[str] | None = None) -> None:
        ui.info(
            "Opening domain registration in your browser. "
            "Sign in to Hetzner and review and confirm the purchase yourself."
        )
        from cli.hetzner import register_domain

        ok = register_domain(domain=ctx.base_domain, nameservers=nameservers)
        if ok:
            ui.action_done("Domain registered")
        else:
            ui.action_fail("Domain registration failed")
            if not ui.confirm("Continue anyway?", default=False):
                raise click.ClickException("Cancelled.")
