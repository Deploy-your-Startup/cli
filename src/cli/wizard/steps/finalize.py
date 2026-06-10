"""Step 4 (fullstack): commit, create GH repo, push secrets, push code, trigger infra."""

from __future__ import annotations

import os
import subprocess
import time

from cli import wizard_output as ui
from cli.bootstrap import install_spawn_startup_skill, register_project_in_startup_factory
from cli.sync_commands import _run_command

from ..base import WizardStep, is_pushed, repo_exists
from ..context import BootstrapContext


def store_vault_password_in_keychain(project_name: str, vault_password: str) -> None:
    """Store the vault password in macOS Keychain."""
    from cli.ansible_commands import keychain_service_name

    service_name = keychain_service_name(project_name)
    user = os.environ.get("USER", "")
    subprocess.run(
        [
            "security", "add-generic-password",
            "-a", user, "-s", service_name,
            "-w", vault_password,
            "-U",  # update if exists
        ],
        check=True,
        capture_output=True,
    )


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
        ui.action_start("spawn-startup Skill installieren...")
        if install_spawn_startup_skill(ctx.project_dir, output_dir=ctx.output_dir):
            ui.action_done("Skill für Claude/Codex/OpenCode installiert")
        else:
            ui.action_done("Kein startup-factory-Kontext erkannt, übersprungen")

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

        ui.action_start("startup-factory Registry aktualisieren...")
        registration_state = register_project_in_startup_factory(
            project_name=ctx.project_name,
            github_username=ctx.github_username,
            output_dir=ctx.output_dir,
        )
        if registration_state == "added":
            ui.action_done("projects.yaml ergänzt")
        elif registration_state == "exists":
            ui.action_done("projects.yaml bereits aktuell")
        else:
            ui.action_done("Nicht im startup-factory Hub, übersprungen")

        # 4f. Trigger infrastructure workflow (retry — GitHub needs time to index)
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

        # 4g. Store vault password in Keychain
        ui.action_start("Vault-Passwort in Keychain speichern...")
        try:
            store_vault_password_in_keychain(ctx.project_name, ctx.vault_password)
            ui.action_done("Vault-Passwort in Keychain gespeichert")
        except subprocess.CalledProcessError:
            ui.action_fail("Keychain-Speicherung fehlgeschlagen")
            ui.warning(f"Vault-Passwort manuell speichern: {ctx.vault_password}")
