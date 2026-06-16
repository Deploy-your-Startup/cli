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


class FinalizeStep(WizardStep):
    number = 4
    name = "Abschluss"

    def check(self, ctx: BootstrapContext) -> bool:
        if ctx.mode != "github":
            return False
        if not ctx.project_dir.exists():
            return False
        if is_pushed(ctx.project_dir) and repo_exists(ctx.full_repo):
            ui.skip_indicator("Code bereits gepusht")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        # 4a. Commit
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

        # 4b. GitHub repo + remote
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

        # 4c. GitHub Actions config
        ui.action_start("GitHub Actions konfigurieren...")
        _run_command(
            ["gh", "api", "-X", "PUT",
             f"repos/{ctx.full_repo}/actions/permissions",
             "-F", "enabled=true", "-f", "allowed_actions=all"],
            cwd=ctx.project_dir, capture_output=True,
        )
        _run_command(
            ["gh", "api", "-X", "PUT",
             f"repos/{ctx.full_repo}/actions/permissions/workflow",
             "-f", "default_workflow_permissions=write",
             "-F", "can_approve_pull_request_reviews=true"],
            cwd=ctx.project_dir, capture_output=True,
        )
        ui.action_done("GitHub Actions konfiguriert")

        # 4d. Vault password as GitHub secret
        if not ctx.vault_password:
            raise click.ClickException(
                "Vault-Passwort fehlt im Bootstrap-Kontext. Bitte Step 3 erneut "
                "ausführen oder das Passwort in der Keychain prüfen."
            )
        ui.action_start("Vault-Passwort als GitHub Secret...")
        _run_command(
            ["gh", "secret", "set", "VAULT_PASSWORD", "--body", ctx.vault_password],
            cwd=ctx.project_dir, capture_output=True,
        )
        ui.action_done("GitHub Secret gesetzt")

        # 4e. Push
        ui.action_start("Push nach GitHub...")
        _run_command(
            ["git", "push", "-u", "origin", "main"],
            cwd=ctx.project_dir, capture_output=True,
        )
        ui.action_done("Gepusht")

        # 4f. Provision. On byos there is nothing to provision in the cloud — the
        # user runs the install/deploy locally against their VPS, so we just print
        # the next steps instead of kicking off the Hetzner infrastructure workflow.
        if ctx.provider == "byos":
            from .project import (
                BYOS_DEPLOY_PUBLIC_KEY_FILE,
                byos_deploy_key_install_command,
            )

            deploy_key_path = ctx.deployment_dir / BYOS_DEPLOY_PUBLIC_KEY_FILE
            deploy_public_key = deploy_key_path.read_text().strip()
            ui.action_done("BYOS — kein Cloud-Provisioning nötig")
            ui.info(
                "Nächste Schritte:\n"
                "  # Deploy-Key einmalig auf den VPS kopieren\n"
                f"  {byos_deploy_key_install_command(ctx, deploy_public_key)}\n"
                "  # Danach lokal deployen\n"
                f"  cd {ctx.deployment_dir}\n"
                "  ./make.sh setup\n"
                "  ./make.sh infrastructure --environment production\n"
                "  ./make.sh deploy --environment production\n"
                "Das installiert k3s auf dem VPS und rollt cert-manager, Postgres "
                "und das Backend aus."
            )
            return

        # 4f (hetzner). Trigger infra workflow (retry — GitHub needs time to index)
        ui.action_start("Infrastructure-Workflow starten...")
        triggered = False
        last_err: str | None = None
        for attempt in range(6):
            if attempt:
                time.sleep(2)
            proc = subprocess.run(
                ["gh", "workflow", "run", "deploy-infrastructure.yml", "--ref", "main"],
                cwd=ctx.project_dir, capture_output=True, text=True,
            )
            if proc.returncode == 0:
                triggered = True
                break
            last_err = (proc.stderr or proc.stdout or "").strip()

        if triggered:
            ui.action_done("Infrastructure-Workflow läuft (siehe Actions-Tab)")
        else:
            ui.action_fail("Workflow-Start fehlgeschlagen")
            ui.warning(
                "Bitte manuell starten: "
                "gh workflow run deploy-infrastructure.yml --ref main"
                + (f" ({last_err})" if last_err else "")
            )

        # 4g. Re-assert vault password in Keychain (already stored in step 3f;
        # this is an idempotent safety net in case it was changed since).
        ui.action_start("Vault-Passwort in Keychain speichern...")
        try:
            store_keychain_password(ctx.project_name, ctx.vault_password)
            ui.action_done("Vault-Passwort in Keychain gespeichert")
        except subprocess.CalledProcessError:
            ui.action_fail("Keychain-Speicherung fehlgeschlagen")
            ui.warning(f"Vault-Passwort manuell speichern: {ctx.vault_password}")
