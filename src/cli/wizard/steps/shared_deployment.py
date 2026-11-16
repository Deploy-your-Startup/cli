"""Settle where the shared deployment workflows come from before creating anything.

Templates that ask for ``deploy_repository`` call ``Deploy-your-Startup/deploy``
directly and are pinned to its latest release. Older templates call a per-owner
``deploy-your-startup`` copy, which this step creates with ``startup sync``.
"""

import subprocess

from cli import wizard_output as ui
from cli.shared_source import DEFAULT_SHARED_REPOSITORY, LEGACY_REPO_NAME
from cli.template_commands import template_kpis, template_uses_deploy
from cli.wizard.context import BootstrapContext

from ..base import WizardStep, repo_exists


def choose_shared_deployment(ctx: BootstrapContext) -> None:
    """Pin a direct template to the latest release unless a ref was requested."""
    ctx.direct_deploy = template_uses_deploy(ctx.template_source, ctx.template_version)
    if ctx.direct_deploy and ctx.deployment_ref == "main":
        from cli.project_update import latest_published_release

        ctx.deployment_release, ctx.deployment_ref = latest_published_release(
            DEFAULT_SHARED_REPOSITORY
        )


def deploy_repository(ctx: BootstrapContext) -> str:
    """``owner/name`` the rendered project's shared workflows call."""
    if ctx.direct_deploy:
        return DEFAULT_SHARED_REPOSITORY
    return f"{ctx.github_username}/{LEGACY_REPO_NAME}"


class SharedDeploymentStep(WizardStep):
    name = "Deployment workflows"
    number = 1

    def check(self, ctx: BootstrapContext) -> bool:
        if ctx.direct_deploy:
            release = ctx.deployment_release or ctx.deployment_ref
            ui.skip_indicator(
                f"Shared deployment: {DEFAULT_SHARED_REPOSITORY} {release}"
            )
            return True
        copy = deploy_repository(ctx)
        if repo_exists(copy):
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
                            f"repos/{copy}/contents/roles/{role}/tasks/main.yml",
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
        from cli.sync_commands import sync_deploy_repo

        ui.action_start("Create shared deployment repository in your GitHub account...")
        sync_deploy_repo(owner=ctx.github_username)
        ui.action_done("Shared deployment workflows ready")
