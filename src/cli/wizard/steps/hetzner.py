"""Step 2 (fullstack): obtain & validate a Hetzner Cloud API token."""

from __future__ import annotations

import click
import httpx

from cli import wizard_output as ui

from ..base import WizardStep
from ..context import BootstrapContext


def validate_hetzner_token(token: str) -> bool:
    """Validate a Hetzner API token via GET /v1/projects."""
    try:
        resp = httpx.get(
            "https://api.hetzner.cloud/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


class HetznerStep(WizardStep):
    number = 2
    name = "Hetzner Cloud"

    def check(self, ctx: BootstrapContext) -> bool:
        from cli.hetzner.credentials import load_token, token_exists

        if not token_exists():
            return False

        token = load_token()
        if token and validate_hetzner_token(token):
            ui.skip_indicator("Token gefunden und validiert")
            ctx.hetzner_token = token
            return True

        ui.warning("Gespeicherter Token ist ungültig.")
        return False

    def run(self, ctx: BootstrapContext) -> None:
        choice = ui.numbered_choice(
            "Wie soll der Hetzner API Token bereitgestellt werden?",
            [
                "Ich habe schon einen Token (einfügen)",
                "Projekt + Token im Browser erstellen",
            ],
        )

        if choice == 1:
            while True:
                token = ui.text_input("Hetzner Cloud API Token", hide_input=True)
                ui.action_start("Token validieren...")
                if validate_hetzner_token(token):
                    ui.action_done("Token validiert")
                    from cli.hetzner.credentials import save_token

                    save_token(token, ctx.project_name)
                    ctx.hetzner_token = token
                    return
                else:
                    ui.error("Token ungültig. Bitte erneut versuchen.")
        else:
            ui.info(
                "Ich öffne den Browser für die Hetzner Cloud Console. "
                "Du musst dich einloggen und ein Projekt + Token erstellen."
            )
            from cli.hetzner import get_or_create_token

            token = get_or_create_token(project_name=ctx.project_name)
            if not token:
                raise click.ClickException(
                    "Konnte keinen Hetzner Token erhalten. "
                    "Versuche es erneut oder nutze --hetzner-token."
                )
            ctx.hetzner_token = token
            ui.action_done("Token erstellt und gespeichert")
