"""Step 4 (fullstack): commit, create GH repo, push secrets, push code, trigger infra."""

from __future__ import annotations

import subprocess
import time

import click

from cli import wizard_output as ui
from cli.sync_commands import _run_command

from ..base import WizardStep, is_pushed, repo_exists
from ..context import BootstrapContext
from ..vault_guard import store_keychain_password


def set_github_vault_secret(project_dir, password: str) -> None:
    """Send the password through stdin; never expose it in argv or errors."""
    result = subprocess.run(
        ["gh", "secret", "set", "VAULT_PASSWORD"],
        cwd=project_dir,
        input=password,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise click.ClickException("Could not set the GitHub vault secret.")


class FinalizeStep(WizardStep):
    number = 4
    name = "Abschluss"

    def check(self, ctx: BootstrapContext) -> bool:
        if ctx.mode != "github":
            return False
        if not ctx.project_dir.exists():
            return False
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ctx.project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if dirty.stdout.strip():
            return False
        if is_pushed(ctx.project_dir) and repo_exists(ctx.full_repo):
            ui.skip_indicator("Code already pushed")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        # 4a. Commit
        ui.action_start("Committing project...")
        _run_command(["git", "add", "-A"], cwd=ctx.project_dir)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ctx.project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            _run_command(
                ["git", "commit", "-m", "bootstrap: configure project"],
                cwd=ctx.project_dir,
            )
            ui.action_done("Committed")
        else:
            ui.action_done("Nothing to commit")

        # 4b. GitHub repo + remote
        subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=ctx.project_dir,
            capture_output=True,
            check=False,
        )
        if not repo_exists(ctx.full_repo):
            ui.action_start("Creating GitHub repository...")
            _run_command(
                ["gh", "repo", "create", ctx.full_repo, "--private", "--source", "."],
                cwd=ctx.project_dir,
            )
            ui.action_done("Repository created")
        else:
            _run_command(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{ctx.full_repo}.git",
                ],
                cwd=ctx.project_dir,
            )

        # 4c. GitHub Actions config
        ui.action_start("Configuring GitHub Actions...")
        _run_command(
            [
                "gh",
                "api",
                "-X",
                "PUT",
                f"repos/{ctx.full_repo}/actions/permissions",
                "-F",
                "enabled=true",
                "-f",
                "allowed_actions=all",
            ],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        _run_command(
            [
                "gh",
                "api",
                "-X",
                "PUT",
                f"repos/{ctx.full_repo}/actions/permissions/workflow",
                "-f",
                "default_workflow_permissions=write",
                "-F",
                "can_approve_pull_request_reviews=true",
            ],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        ui.action_done("GitHub Actions configured")

        # 4d. Vault password as GitHub secret
        if not ctx.vault_password:
            raise click.ClickException(
                "Vault password is missing from setup. Rerun the project step "
                "or check the password in Keychain."
            )
        ui.action_start("Saving vault password as a GitHub secret...")
        set_github_vault_secret(ctx.project_dir, ctx.vault_password)
        ui.action_done("GitHub secret saved")

        # 4e. Deploy key onto the VPS. Before the push on purpose: pushing starts
        # the deploy workflow, and that workflow logs into the server with this
        # key. Installing it afterwards means the first run always fails.
        if ctx.provider == "byos":
            from .project import (
                BYOS_DEPLOY_PUBLIC_KEY_FILE,
                byos_deploy_key_install_command,
                install_byos_deploy_key,
            )

            deploy_public_key = (
                (ctx.deployment_dir / BYOS_DEPLOY_PUBLIC_KEY_FILE).read_text().strip()
            )
            ui.action_start("Installing deploy key on the server...")
            if install_byos_deploy_key(ctx, deploy_public_key):
                ui.action_done(f"Deploy key on {ctx.byos_host} installed")
            else:
                ui.info(
                    f"Could not reach {ctx.byos_ssh_user}@{ctx.byos_host} over SSH. "
                    "Run this from a machine that can reach "
                    "the server before deploying:\n"
                    f"  {byos_deploy_key_install_command(ctx, deploy_public_key)}"
                )

        # 4f. Push
        ui.action_start("Pushing to GitHub...")
        _run_command(
            ["git", "push", "-u", "origin", "main"],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        ui.action_done("Pushed")

        # 4g. Provision. On byos there is nothing to provision in the cloud — the
        # user runs the install/deploy locally against their VPS, so we just print
        # the next steps instead of kicking off the Hetzner infrastructure workflow.
        if ctx.cluster_config:
            ui.info(
                "Attached to the existing cluster. Application workflows deploy into this startup's namespace; run cluster operations from "
                + ctx.cluster_config["owner"]
                + "."
            )
            return
        if ctx.provider == "byos":
            ui.action_done("BYOS — no cloud provisioning needed")
            ui.info(
                "Next steps:\n"
                f"  cd {ctx.deployment_dir}\n"
                "  ./make.sh setup\n"
                "  ./make.sh infrastructure --environment production\n"
                "  ./make.sh deploy --environment production\n"
                "This installs k3s on the server and deploys cert-manager, Postgres "
                "and the backend."
            )
            return

        # 4f (hetzner). Trigger infra workflow (retry — GitHub needs time to index)
        ui.action_start("Requesting infrastructure deployment...")
        triggered = False
        last_err: str | None = None
        for attempt in range(6):
            if attempt:
                time.sleep(2)
            proc = subprocess.run(
                ["gh", "workflow", "run", "deploy-infrastructure.yml", "--ref", "main"],
                cwd=ctx.project_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                triggered = True
                break
            last_err = (proc.stderr or proc.stdout or "").strip()

        if triggered:
            ui.action_done("Infrastructure deployment requested")
            ui.info(
                "GitHub is assigning a runner; an Actions outage may "
                "delay the start. Status:\n"
                f"  https://github.com/{ctx.full_repo}/actions"
            )
        else:
            ui.action_fail("Could not start workflow")
            raise click.ClickException(
                "Start it manually: "
                "gh workflow run deploy-infrastructure.yml --ref main"
                + (f" ({last_err})" if last_err else "")
            )

        # 4g. Re-assert vault password in Keychain (already stored in step 3f;
        # this is an idempotent safety net in case it was changed since).
        ui.action_start("Saving vault password in Keychain...")
        try:
            store_keychain_password(ctx.project_name, ctx.vault_password)
            ui.action_done("Vault password saved in Keychain")
        except click.ClickException:
            ui.action_fail("Could not save to Keychain")
            ui.warning("Could not save the vault password to Keychain again.")
