"""Attached startups: create their Hetzner DNS zone before the domain is bought.

A .de registration fails at DENIC while the assigned nameservers serve no zone
for the domain, so the zone and its A records must exist before the order. The
zone lives in the startup's own Hetzner project (DNS only, no servers); its
token sits in the startup's Vault for later `startup ansible dns` runs.
"""

from __future__ import annotations

import click

from cli import wizard_output as ui

from ..base import WizardStep
from ..context import BootstrapContext


class AttachedDnsStep(WizardStep):
    number = 4
    name = "DNS zone"

    def check(self, ctx: BootstrapContext) -> bool:
        return False

    def run(self, ctx: BootstrapContext) -> None:
        from cli.ansible_commands import run_dns

        if not ctx.vault_password:
            raise click.ClickException("The project Vault must exist before DNS setup.")
        ui.action_start("Creating the Hetzner DNS zone through Ansible...")
        run_dns(
            vault_password=ctx.vault_password,
            environment="production",
            working_directory=str(ctx.deployment_dir),
            version=ctx.deployment_ref,
        )
        ui.action_done(f"{ctx.base_domain} points at the shared cluster")
