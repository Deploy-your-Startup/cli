"""Step 3 (pitch): render the pitch template with Copier."""

from __future__ import annotations

from cli import wizard_output as ui
from cli.sync_commands import _run_command
from cli.template_commands import ANSWERS_FILE, render_project

from ..base import WizardStep
from ..context import BootstrapContext


class PitchProjectStep(WizardStep):
    number = 3
    name = "Create project"

    def check(self, ctx: BootstrapContext) -> bool:
        if not (ctx.project_dir / ANSWERS_FILE).exists():
            return False
        ui.skip_indicator(f"Project {ctx.project_name} already configured")
        return True

    def run(self, ctx: BootstrapContext) -> None:
        if not ctx.project_dir.exists():
            ctx.project_dir.mkdir(parents=True)
        if not (ctx.project_dir / ".git").exists():
            _run_command(["git", "init", "-b", "main"], cwd=ctx.project_dir)

        ui.action_start("Rendering landing-page template...")
        render_project(
            ctx.project_dir,
            {
                "§§deploy_your_startup.project_name§§": ctx.project_name,
                "§§deploy_your_startup.base_domain§§": ctx.base_domain,
                "§§deploy_your_startup.github_username§§": ctx.github_username,
            },
            source=ctx.template_source,
            version=ctx.template_version,
        )
        ui.action_done("Project configured")
