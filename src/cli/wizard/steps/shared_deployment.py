"""Ensure reusable deployment workflows exist before application creation."""

import subprocess

from cli import wizard_output as ui
from cli.sync_commands import sync_deploy_repo
from cli.template_commands import template_kpis
from cli.wizard.context import BootstrapContext

from ..base import WizardStep, repo_exists


class SharedDeploymentStep(WizardStep):
    name = "Deployment workflows"
    number = 1

    def check(self, ctx: BootstrapContext) -> bool:
        if repo_exists(f"{ctx.github_username}/deploy-your-startup"):
            if ctx.kind != "pitch" and template_kpis(
                ctx.template_source, ctx.template_version
            ):
                for role in ("oauth2-proxy", "kpis"):
                    result = subprocess.run(
                        [
                            "gh",
                            "api",
                            "-X",
                            "GET",
                            f"repos/{ctx.github_username}/deploy-your-startup/contents/roles/{role}/tasks/main.yml",
                            "-f",
                            f"ref={ctx.deployment_ref}",
                        ],
                        capture_output=True,
                        check=False,
                    )
                    if result.returncode:
                        return False
            ui.skip_indicator("Shared deployment repository exists")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        ui.action_start("Create shared deployment repository in your GitHub account...")
        sync_deploy_repo(owner=ctx.github_username)
        ui.action_done("Shared deployment workflows ready")
