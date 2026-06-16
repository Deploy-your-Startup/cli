"""Step 2 (fullstack, cloud_provider=ovh): obtain an OpenStack clouds.yaml.

The OVH counterpart to HetznerStep. OVH Public Cloud is OpenStack, so the
compute credential is a clouds.yaml (an Application Credential) rather than a
single token. It is captured via the browser flow or supplied as a file.
"""

from __future__ import annotations

from pathlib import Path

import click

from cli import wizard_output as ui

from ..base import WizardStep
from ..context import BootstrapContext


class OvhStep(WizardStep):
    number = 2
    name = "OVH Public Cloud"

    def check(self, ctx: BootstrapContext) -> bool:
        from cli.ovh import validate_and_normalize_clouds_yaml
        from cli.ovh.credentials import clouds_yaml_exists, load_clouds_yaml

        if not clouds_yaml_exists():
            return False
        normalized = validate_and_normalize_clouds_yaml(load_clouds_yaml() or "")
        if normalized:
            ui.skip_indicator("clouds.yaml gefunden und validiert")
            ctx.openstack_clouds_yaml = normalized
            return True
        ui.warning("Gespeicherte clouds.yaml ist ungültig.")
        return False

    def run(self, ctx: BootstrapContext) -> None:
        from cli.ovh import validate_and_normalize_clouds_yaml
        from cli.ovh.credentials import save_clouds_yaml

        choice = ui.numbered_choice(
            "Wie soll die OVH clouds.yaml (OpenStack-Credential) bereitgestellt werden?",
            [
                "Ich habe schon eine clouds.yaml (Pfad angeben)",
                "Application Credential im Browser erstellen",
            ],
        )

        if choice == 1:
            while True:
                path = ui.text_input("Pfad zur clouds.yaml")
                try:
                    raw = Path(path).expanduser().read_text(encoding="utf-8")
                except OSError as exc:
                    ui.error(f"Datei nicht lesbar: {exc}")
                    continue
                normalized = validate_and_normalize_clouds_yaml(raw)
                if normalized:
                    save_clouds_yaml(normalized)
                    ctx.openstack_clouds_yaml = normalized
                    ui.action_done("clouds.yaml validiert")
                    return
                ui.error("Keine gültige clouds.yaml (auth_url fehlt). Bitte erneut.")
        else:
            ui.info(
                "Ich öffne den Browser für OpenStack Horizon. Logge dich ein, "
                "erstelle ein Application Credential und lade die clouds.yaml herunter."
            )
            from cli.ovh import get_or_create_clouds_yaml

            clouds_yaml = get_or_create_clouds_yaml(project_name=ctx.project_name)
            if not clouds_yaml:
                raise click.ClickException(
                    "Konnte keine clouds.yaml erhalten. Versuche es erneut oder "
                    "gib den Pfad zu einer heruntergeladenen clouds.yaml an."
                )
            ctx.openstack_clouds_yaml = clouds_yaml
            ui.action_done("clouds.yaml erstellt und gespeichert")
