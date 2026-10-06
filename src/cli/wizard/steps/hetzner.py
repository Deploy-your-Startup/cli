"""Step 2 (fullstack): obtain & validate a Hetzner Cloud API token."""

from __future__ import annotations

import click
import httpx

from cli import wizard_output as ui

from ..base import WizardStep
from ..context import BootstrapContext


def validate_hetzner_token(token: str) -> bool:
    """Validate a project-scoped Hetzner API token with a lightweight read."""
    try:
        resp = httpx.get(
            "https://api.hetzner.cloud/v1/servers",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 1},
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
            ui.skip_indicator("Saved token verified")
            ctx.hetzner_token = token
            return True

        ui.warning("The saved token is invalid.")
        return False

    def run(self, ctx: BootstrapContext) -> None:
        # A token handed in on the command line answers this question already.
        if ctx.hetzner_token:
            ui.action_start("Checking token...")
            if not validate_hetzner_token(ctx.hetzner_token):
                raise click.ClickException("The given Hetzner token is not valid.")
            ui.action_done("Token verified")
            from cli.hetzner.credentials import save_token

            save_token(ctx.hetzner_token, ctx.project_name)
            return

        # Unattended: create project and token in the browser, which is the
        # only path that needs no further input.
        choice = (
            2
            if ctx.non_interactive
            else ui.numbered_choice(
                "How would you like to connect Hetzner?",
                [
                    "Paste an existing token",
                    "Create a project and token in the browser",
                ],
            )
        )

        if choice == 1:
            while True:
                token = ui.text_input("Hetzner Cloud API Token", hide_input=True)
                ui.action_start("Checking token...")
                if validate_hetzner_token(token):
                    ui.action_done("Token verified")
                    from cli.hetzner.credentials import save_token

                    save_token(token, ctx.project_name)
                    ctx.hetzner_token = token
                    return
                else:
                    ui.error("Invalid token. Please try again.")
        else:
            ui.info(
                "Opening Hetzner Cloud Console in your browser. "
                "Sign in and create a project and token."
            )
            from cli.hetzner import get_or_create_token

            token = get_or_create_token(project_name=ctx.project_name)
            if not token:
                raise click.ClickException(
                    "Could not reach keinen Hetzner Token erhalten. "
                    "Versuche es erneut oder nutze --hetzner-token."
                )
            ctx.hetzner_token = token
            ui.action_done("Token created and saved")
