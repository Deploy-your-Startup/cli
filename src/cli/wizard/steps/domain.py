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
        # Fullstack: ask; skip the buy flow if the user already owns it.
        choice = ui.numbered_choice(
            f'Besitzt du "{ctx.base_domain}" bereits?',
            [
                "Ja, die Domain gehört mir",
                "Nein, ich möchte sie jetzt bei Hetzner kaufen",
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
                f'"{ctx.base_domain}" ist eine Subdomain einer bereits auf '
                "Cloudflare delegierten Zone — keine Domain-Registrierung oder "
                "Nameserver-Umstellung nötig. DNS-Records werden im nächsten "
                "Schritt in der bestehenden Zone angelegt."
            )
            ui.action_done("Subdomain — keine Nameserver-Umstellung nötig")
            return

        nameservers = ctx.cloudflare_nameservers
        if not nameservers:
            raise click.ClickException(
                "Cloudflare-Nameserver fehlen — der Cloudflare-Schritt muss "
                "zuerst laufen."
            )

        choice = ui.numbered_choice(
            f'Besitzt du "{ctx.base_domain}" bereits?',
            [
                "Ja, bei Hetzner registriert",
                "Ja, bei einem anderen Registrar",
                "Nein, jetzt bei Hetzner kaufen",
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
            "Ich öffne KonsoleH, um die Nameserver der Domain auf Cloudflare "
            "umzustellen. Bitte im Browser einloggen und die Änderung speichern."
        )
        from cli.hetzner import set_domain_nameservers

        ok = set_domain_nameservers(
            domain=ctx.base_domain, nameservers=nameservers
        )
        if ok:
            ui.action_done("Nameserver auf Cloudflare umgestellt")
        else:
            ui.action_fail("Nameserver-Umstellung fehlgeschlagen")
            if not ui.confirm("Trotzdem weitermachen?", default=False):
                raise click.ClickException("Abgebrochen.")

    def _manual_nameservers(
        self, ctx: BootstrapContext, nameservers: list[str]
    ) -> None:
        ns_lines = "\n".join(f"  • {ns}" for ns in nameservers)
        ui.info(
            "Bitte setze bei deinem Registrar die Nameserver der Domain auf:\n"
            f"{ns_lines}\n"
            "Danach übernimmt Cloudflare DNS für die Domain."
        )
        ui.text_input(
            "Enter drücken, sobald die Nameserver gesetzt sind",
            default="",
            show_default=False,
        )
        ui.action_done("Nameserver-Hinweis bestätigt")

    # ── Buy a fresh domain at Hetzner ────────────────────────────────

    def _buy(
        self, ctx: BootstrapContext, nameservers: list[str] | None = None
    ) -> None:
        ui.info(
            "Ich öffne den Browser für die Registrierung. "
            "Du musst dich bei Hetzner einloggen und den Kauf bestätigen."
        )
        from cli.hetzner import register_domain

        ok = register_domain(domain=ctx.base_domain, nameservers=nameservers)
        if ok:
            ui.action_done("Domain registriert")
        else:
            ui.action_fail("Domain-Registrierung fehlgeschlagen")
            if not ui.confirm("Trotzdem weitermachen?", default=False):
                raise click.ClickException("Abgebrochen.")
