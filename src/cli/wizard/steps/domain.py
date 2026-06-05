"""Step 1: ensure the user owns the base domain (or guides them to buy it)."""

from __future__ import annotations

import click

from cli import wizard_output as ui

from ..base import WizardStep
from ..context import BootstrapContext


class DomainStep(WizardStep):
    number = 1
    name = "Domain"

    def check(self, ctx: BootstrapContext) -> bool:
        # Domain check is always interactive — we ask the user
        choice = ui.numbered_choice(
            f'Besitzt du "{ctx.base_domain}" bereits?',
            [
                "Ja, die Domain gehört mir",
                "Nein, ich möchte sie jetzt bei Hetzner kaufen",
            ],
        )
        return choice == 1

    def run(self, ctx: BootstrapContext) -> None:
        ui.info(
            "Ich öffne den Browser für die Registrierung. "
            "Du musst dich bei Hetzner einloggen und den Kauf bestätigen."
        )
        from cli.hetzner import register_domain

        ok = register_domain(domain=ctx.base_domain)
        if ok:
            ui.action_done("Domain registriert")
        else:
            ui.action_fail("Domain-Registrierung fehlgeschlagen")
            if not ui.confirm("Trotzdem weitermachen?", default=False):
                raise click.ClickException("Abgebrochen.")
