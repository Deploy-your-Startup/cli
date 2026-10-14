"""Confirm the authenticated GitHub owner before creating startup resources."""

import os
import subprocess

import click

from cli import wizard_output as ui


def confirm_github_owner(project_name: str) -> str:
    """Reuse a login, or delegate account changes to GitHub CLI's native login."""
    login_completed = False
    while True:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "user", "-q", ".login"],
            capture_output=True,
            text=True,
            check=False,
        )
        owner = result.stdout.strip() if result.returncode == 0 else ""
        if owner:
            ui.info(f"GitHub account: {owner}")
            if ui.confirm(
                f"Create the repository under {owner}/{project_name}?", default=True
            ):
                return owner
        else:
            if login_completed:
                raise click.ClickException(
                    "Could not verify the GitHub account after login. Check your "
                    "connection and gh auth status, then retry. "
                    "No project resources were created."
                )
            ui.info("GitHub is not connected. Sign in to continue.")

        # Environment tokens override gh's stored login. Starting OAuth here
        # would not change the effective account used by later subprocesses.
        if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
            raise click.ClickException(
                "GitHub authentication is controlled by GH_TOKEN or GITHUB_TOKEN. "
                "Update or unset those variables, then rerun startup bootstrap "
                "with the same project choices. No project resources were created."
            )
        ui.info("Choose the GitHub account to use in gh auth login.")
        login = subprocess.run(
            [
                "gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--git-protocol",
                "https",
                "--scopes",
                "repo,workflow,read:packages,write:packages",
            ],
            check=False,
        )
        if login.returncode:
            raise click.ClickException(
                "GitHub login did not finish. CLI installation is unchanged; "
                "no project resources were created. Rerun startup bootstrap when ready."
            )
        setup = subprocess.run(
            ["gh", "auth", "setup-git", "--hostname", "github.com"], check=False
        )
        if setup.returncode:
            raise click.ClickException(
                "Could not configure GitHub Git credentials. Run gh auth setup-git "
                "and retry. No project resources were created."
            )
        login_completed = True
