"""Ensure reusable deployment workflows exist before application creation."""

from cli import wizard_output as ui
from cli.sync_commands import sync_deploy_repo

from ..base import WizardStep, repo_exists


class SharedDeploymentStep(WizardStep):
    name = "Deployment workflows"
    number = 1

    def check(self, ctx):
        if repo_exists(f"{ctx.github_username}/deploy-your-startup"):
            ui.skip_indicator("Shared deployment repository exists")
            return True
        return False

    def run(self, ctx):
        ui.action_start("Create shared deployment repository in your GitHub account...")
        sync_deploy_repo(owner=ctx.github_username)
        ui.action_done("Shared deployment workflows ready")
