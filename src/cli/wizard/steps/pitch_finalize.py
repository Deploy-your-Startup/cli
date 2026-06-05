"""Step 4 (pitch): commit, create GH repo, push CF secrets, push code."""

from __future__ import annotations

import subprocess

from cli import wizard_output as ui
from cli.sync_commands import _run_command

from ..base import WizardStep, is_pushed, repo_exists
from ..context import BootstrapContext


class PitchFinalizeStep(WizardStep):
    number = 4
    name = "Abschluss"

    def check(self, ctx: BootstrapContext) -> bool:
        if not ctx.project_dir.exists():
            return False
        if is_pushed(ctx.project_dir) and repo_exists(ctx.full_repo):
            ui.skip_indicator("Code bereits gepusht")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        ui.action_start("Code committen...")
        _run_command(["git", "add", "-A"], cwd=ctx.project_dir)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ctx.project_dir, capture_output=True, text=True,
        )
        if status.stdout.strip():
            _run_command(
                ["git", "commit", "-m", "bootstrap: configure project"],
                cwd=ctx.project_dir,
            )
            ui.action_done("Committed")
        else:
            ui.action_done("Nichts zu committen")

        subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=ctx.project_dir, capture_output=True,
        )
        if not repo_exists(ctx.full_repo):
            ui.action_start("GitHub-Repository erstellen...")
            _run_command(
                ["gh", "repo", "create", ctx.full_repo, "--private", "--source", "."],
                cwd=ctx.project_dir,
            )
            ui.action_done("Repository erstellt")
        else:
            _run_command(
                ["git", "remote", "add", "origin",
                 f"https://github.com/{ctx.full_repo}.git"],
                cwd=ctx.project_dir,
            )

        ui.action_start("Cloudflare Secrets setzen...")
        _run_command(
            ["gh", "secret", "set", "CLOUDFLARE_API_TOKEN",
             "--body", ctx.cloudflare_api_token],
            cwd=ctx.project_dir, capture_output=True,
        )
        _run_command(
            ["gh", "secret", "set", "CLOUDFLARE_ACCOUNT_ID",
             "--body", ctx.cloudflare_account_id],
            cwd=ctx.project_dir, capture_output=True,
        )
        ui.action_done("Secrets gesetzt")

        ui.action_start("Push nach GitHub...")
        _run_command(
            ["git", "push", "-u", "origin", "main"],
            cwd=ctx.project_dir, capture_output=True,
        )
        ui.action_done("Gepusht")

        ui.info(
            f"Custom Domain {ctx.base_domain} musst du noch in Cloudflare Pages "
            "verknüpfen:\n"
            f"  https://dash.cloudflare.com → Pages → {ctx.project_name} "
            "→ Custom domains → Set up a custom domain"
        )
