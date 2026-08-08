"""Step 3 (pitch): clone pitch-template, replace minimal placeholders."""

from __future__ import annotations

import shutil

from cli import wizard_output as ui
from cli.bootstrap import PITCH_TEMPLATE_REPO, TEMPLATE_OWNER
from cli.sync_commands import _replace_placeholders, _run_command

from ..base import WizardStep, has_placeholders
from ..context import BootstrapContext


class PitchProjectStep(WizardStep):
    number = 3
    name = "Projekt erstellen"

    def check(self, ctx: BootstrapContext) -> bool:
        if not ctx.project_dir.exists():
            return False
        if has_placeholders(ctx.project_dir):
            return False
        ui.skip_indicator(f"Projekt {ctx.project_name} bereits konfiguriert")
        return True

    def run(self, ctx: BootstrapContext) -> None:
        if not ctx.project_dir.exists():
            ui.action_start("Pitch-Template klonen...")
            _run_command(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    f"https://github.com/{TEMPLATE_OWNER}/{PITCH_TEMPLATE_REPO}.git",
                    str(ctx.project_dir),
                ],
                cwd=ctx.output_dir,
            )
            shutil.rmtree(ctx.project_dir / ".git")
            _run_command(["git", "init", "-b", "main"], cwd=ctx.project_dir)
            ui.action_done("Template geklont")

        ui.action_start("Placeholders ersetzen...")
        _replace_placeholders(
            ctx.project_dir,
            {
                "§§deploy_your_startup.project_name§§": ctx.project_name,
                "§§deploy_your_startup.base_domain§§": ctx.base_domain,
                "§§deploy_your_startup.github_username§§": ctx.github_username,
            },
        )
        ui.action_done("Projekt konfiguriert")
