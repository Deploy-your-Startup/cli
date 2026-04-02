"""Bootstrap wizard — guided 4-step pipeline for new startups.

Orchestrates: Domain → Hetzner Token → Project → Finalize
Each step has check() for idempotency and run() for execution.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import click
import httpx

from cli import wizard_output as ui
from cli.bootstrap import (
    TEMPLATE_OWNER,
    TEMPLATE_REPO,
    TEMPLATE_VAULT_PASSWORD,
    _generate_docker_config_b64,
    _generate_ssh_keypair,
)
from cli.sync_commands import _replace_placeholders, _run_command


# ── Data container passed between steps ──────────────────────────────


@dataclass
class BootstrapContext:
    """Mutable state shared across all wizard steps."""

    project_name: str
    base_domain: str
    additional_domains: str
    github_username: str
    postgres_version: str
    sentry_dsn: str
    output_dir: Path
    mode: str = "github"
    docker_registry_host: str = "ghcr.io"

    # Populated by steps
    hetzner_token: str | None = None
    vault_password: str | None = None

    @property
    def project_dir(self) -> Path:
        return self.output_dir / self.project_name

    @property
    def deployment_dir(self) -> Path:
        return self.project_dir / "deployment"

    @property
    def full_repo(self) -> str:
        return f"{self.github_username}/{self.project_name}"

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.full_repo}"


# ── Base step ────────────────────────────────────────────────────────


class WizardStep(ABC):
    """Base class for a bootstrap wizard step."""

    number: int
    name: str

    @abstractmethod
    def check(self, ctx: BootstrapContext) -> bool:
        """Return True if this step can be skipped (already done)."""

    @abstractmethod
    def run(self, ctx: BootstrapContext) -> None:
        """Execute the step. Raise on failure."""


# ── Step 1: Domain ───────────────────────────────────────────────────


class DomainStep(WizardStep):
    number = 1
    name = "Domain"

    def check(self, ctx: BootstrapContext) -> bool:
        # Domain check is always interactive — we ask the user
        choice = ui.numbered_choice(
            f'Besitzt du "{ctx.base_domain}" bereits?',
            [
                "Ja, die Domain gehört mir",
                "Nein, ich möchte sie jetzt bei Hetzner kaufen",
            ],
        )
        return choice == 1  # skip if user already owns it

    def run(self, ctx: BootstrapContext) -> None:
        ui.info(
            "Ich öffne den Browser für die Registrierung. "
            "Du musst dich bei Hetzner einloggen und den Kauf bestätigen."
        )
        from cli.hetzner import register_domain

        ok = register_domain(domain=ctx.base_domain)
        if ok:
            ui.action_done("Domain registriert")
        else:
            ui.action_fail("Domain-Registrierung fehlgeschlagen")
            if not ui.confirm("Trotzdem weitermachen?", default=False):
                raise click.ClickException("Abgebrochen.")


# ── Step 2: Hetzner Token ───────────────────────────────────────────


def _validate_hetzner_token(token: str) -> bool:
    """Validate a Hetzner API token via GET /v1/projects."""
    try:
        resp = httpx.get(
            "https://api.hetzner.cloud/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


class HetznerStep(WizardStep):
    number = 2
    name = "Hetzner Cloud"

    def check(self, ctx: BootstrapContext) -> bool:
        from cli.hetzner.credentials import load_token, token_exists

        if not token_exists():
            return False

        token = load_token()
        if token and _validate_hetzner_token(token):
            ui.skip_indicator("Token gefunden und validiert")
            ctx.hetzner_token = token
            return True

        ui.warning("Gespeicherter Token ist ungültig.")
        return False

    def run(self, ctx: BootstrapContext) -> None:
        choice = ui.numbered_choice(
            "Wie soll der Hetzner API Token bereitgestellt werden?",
            [
                "Ich habe schon einen Token (einfügen)",
                "Projekt + Token im Browser erstellen",
            ],
        )

        if choice == 1:
            # Manual paste with validation loop
            while True:
                token = ui.text_input("Hetzner Cloud API Token", hide_input=True)
                ui.action_start("Token validieren...")
                if _validate_hetzner_token(token):
                    ui.action_done("Token validiert")
                    # Save temporarily
                    from cli.hetzner.credentials import save_token

                    save_token(token, ctx.project_name)
                    ctx.hetzner_token = token
                    return
                else:
                    ui.error("Token ungültig. Bitte erneut versuchen.")
        else:
            # Browser automation
            ui.info(
                "Ich öffne den Browser für die Hetzner Cloud Console. "
                "Du musst dich einloggen und ein Projekt + Token erstellen."
            )
            from cli.hetzner import get_or_create_token

            token = get_or_create_token(project_name=ctx.project_name)
            if not token:
                raise click.ClickException(
                    "Konnte keinen Hetzner Token erhalten. "
                    "Versuche es erneut oder nutze --hetzner-token."
                )
            ctx.hetzner_token = token
            ui.action_done("Token erstellt und gespeichert")


# ── Step 3: Project ──────────────────────────────────────────────────


def _repo_exists(full_repo: str) -> bool:
    """Check if a GitHub repo exists via gh CLI."""
    try:
        subprocess.run(
            ["gh", "repo", "view", full_repo],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _has_placeholders(project_dir: Path) -> bool:
    """Check if the project still contains §§deploy_your_startup placeholders."""
    if not project_dir.exists():
        return False
    for root, _dirs, files in os.walk(project_dir):
        for f in files:
            fp = Path(root) / f
            try:
                if "§§deploy_your_startup" in fp.read_text(errors="ignore"):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
    return False


class ProjectStep(WizardStep):
    number = 3
    name = "Projekt erstellen"

    def check(self, ctx: BootstrapContext) -> bool:
        if ctx.mode != "github":
            return False

        if not ctx.project_dir.exists():
            # Maybe repo exists on GitHub but not cloned locally
            if _repo_exists(ctx.full_repo):
                ui.info(
                    f"Repository {ctx.full_repo} existiert, aber ist nicht lokal geklont."
                )
                return False
            return False

        if _has_placeholders(ctx.project_dir):
            ui.info(
                "Repository existiert, hat aber noch Placeholder — konfiguriere neu..."
            )
            return False

        # Repo exists locally and is configured
        ui.skip_indicator(f"Projekt {ctx.project_name} bereits konfiguriert")
        return True

    def run(self, ctx: BootstrapContext) -> None:
        need_clone = not ctx.project_dir.exists()

        # 3a. Clone template
        if need_clone:
            if ctx.mode == "github":
                ui.action_start("Repository aus Template erstellen...")
                _run_command(
                    [
                        "gh",
                        "repo",
                        "create",
                        ctx.full_repo,
                        "--template",
                        f"{TEMPLATE_OWNER}/{TEMPLATE_REPO}",
                        "--private",
                        "--clone",
                    ],
                    cwd=ctx.output_dir,
                )
                ui.action_done("Repository erstellt")
            else:
                import shutil

                ui.action_start("Template klonen...")
                _run_command(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        f"https://github.com/{TEMPLATE_OWNER}/{TEMPLATE_REPO}.git",
                        str(ctx.project_dir),
                    ],
                    cwd=ctx.output_dir,
                )
                shutil.rmtree(ctx.project_dir / ".git")
                _run_command(["git", "init"], cwd=ctx.project_dir)
                ui.action_done("Template geklont")

        # 3b. SSH Keys
        ui.action_start("SSH Keys generieren...")
        with tempfile.TemporaryDirectory(prefix="bootstrap-ssh-") as ssh_tmp:
            ci_private_key, ci_public_key = _generate_ssh_keypair(
                ctx.project_name, Path(ssh_tmp)
            )
        user_public_key = ci_public_key
        ui.action_done("SSH Keys generiert")

        # 3c. Placeholders
        ui.action_start("Projekt konfigurieren...")
        if ctx.additional_domains:
            domains_list = [
                d.strip() for d in ctx.additional_domains.split(",") if d.strip()
            ]
            additional_domains_yaml = "\n".join(f"  - {d}" for d in domains_list)
        else:
            additional_domains_yaml = "[]"

        replacements = {
            "§§deploy_your_startup.project_name§§": ctx.project_name,
            "§§deploy_your_startup.base_domain§§": ctx.base_domain,
            "§§deploy_your_startup.additional_domains§§": additional_domains_yaml,
            "§§deploy_your_startup.github_username§§": ctx.github_username,
            "§§deploy_your_startup.docker_registry_host§§": f"{ctx.docker_registry_host}/{ctx.github_username}",
            "§§deploy_your_startup.postgres_version§§": ctx.postgres_version,
            "§§deploy_your_startup.ci_key§§": ci_public_key,
            "§§deploy_your_startup.user_key§§": user_public_key,
        }
        _replace_placeholders(ctx.project_dir, replacements)
        ui.action_done("Projekt konfiguriert")

        # 3d. Vault secrets
        ui.action_start("Secrets verschlüsseln...")
        from cli.vault.common import generate_random_secret
        from cli.update_vault_secrets import update_secrets as update_vault_secrets

        ctx.vault_password = generate_random_secret(length=48)
        docker_config_b64 = _generate_docker_config_b64(ctx.github_username)

        field_random = ["k3s_token", "backend_db_password", "postgres_admin_password"]
        field_set = [
            ("postgres_admin_username", "admin"),
            ("docker_config_json_b64", docker_config_b64),
        ]
        if ctx.sentry_dsn:
            field_set.append(("backend_sentry_dsn", ctx.sentry_dsn))

        file_content = [
            ("ci_ssh_key", ci_private_key),
            ("hcloud_token_production", ctx.hetzner_token),
        ]

        ok, _, pw_failed = update_vault_secrets(
            repo=str(ctx.deployment_dir),
            vault_password=TEMPLATE_VAULT_PASSWORD,
            vault_fields=field_random,
            set_field=field_set,
            set_file_content=file_content,
        )
        if not ok or pw_failed:
            raise click.ClickException("Fehler beim Verschlüsseln der Secrets.")
        ui.action_done("Secrets verschlüsselt")

        # 3e. Rotate vault password
        ui.action_start("Vault-Passwort rotieren...")
        from cli.rotate_vault import rotate_vault_password as rotate_vault

        rotated = rotate_vault(
            repo=str(ctx.deployment_dir),
            old_password=TEMPLATE_VAULT_PASSWORD,
            new_password=ctx.vault_password,
            strict=True,
        )
        if not rotated:
            raise click.ClickException("Fehler beim Rotieren des Vault-Passworts.")
        ui.action_done("Vault-Passwort rotiert")

        # 3f. Token cleanup — immediately after vault encryption
        ui.action_start("Hetzner Token aufräumen...")
        from cli.hetzner.credentials import delete_token

        delete_token()
        ui.action_done("Hetzner Token aufgeräumt 🗑️")


# ── Step 4: Finalize ─────────────────────────────────────────────────


def _is_pushed(project_dir: Path) -> bool:
    """Check if local branch is up to date with remote."""
    try:
        result = subprocess.run(
            ["git", "status", "--branch", "--porcelain=v2"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        # If there's an "ahead" marker, we're not pushed yet
        for line in result.stdout.splitlines():
            if line.startswith("# branch.ab"):
                # Format: # branch.ab +N -M
                parts = line.split()
                ahead = int(parts[2].lstrip("+"))
                return ahead == 0
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    return False


def _store_vault_password_in_keychain(project_name: str, vault_password: str) -> None:
    """Store the vault password in macOS Keychain."""
    from cli.ansible_commands import keychain_service_name

    service_name = keychain_service_name(project_name)
    user = os.environ.get("USER", "")
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-a",
            user,
            "-s",
            service_name,
            "-w",
            vault_password,
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
        if _is_pushed(ctx.project_dir):
            ui.skip_indicator("Code bereits gepusht")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        # 4a. Commit
        ui.action_start("Code committen...")
        _run_command(["git", "add", "-A"], cwd=ctx.project_dir)
        _run_command(
            ["git", "commit", "-m", "bootstrap: configure project"],
            cwd=ctx.project_dir,
        )
        ui.action_done("Committed")

        if ctx.mode == "github":
            # 4b. GitHub Actions config
            ui.action_start("GitHub Actions konfigurieren...")
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
            ui.action_done("GitHub Actions konfiguriert")

            # 4c. Vault password as GitHub secret
            ui.action_start("Vault-Passwort als GitHub Secret...")
            _run_command(
                ["gh", "secret", "set", "VAULT_PASSWORD", "--body", ctx.vault_password],
                cwd=ctx.project_dir,
                capture_output=True,
            )
            ui.action_done("GitHub Secret gesetzt")

            # 4d. Push
            ui.action_start("Push nach GitHub...")
            _run_command(
                ["git", "push", "origin", "main"],
                cwd=ctx.project_dir,
                capture_output=True,
            )
            ui.action_done("Gepusht")

        # 4e. Store vault password in Keychain
        ui.action_start("Vault-Passwort in Keychain speichern...")
        try:
            _store_vault_password_in_keychain(ctx.project_name, ctx.vault_password)
            ui.action_done("Vault-Passwort in Keychain gespeichert")
        except subprocess.CalledProcessError:
            ui.action_fail("Keychain-Speicherung fehlgeschlagen")
            ui.warning(f"Vault-Passwort manuell speichern: {ctx.vault_password}")


# ── Pipeline runner ──────────────────────────────────────────────────


STEPS: list[type[WizardStep]] = [DomainStep, HetznerStep, ProjectStep, FinalizeStep]
TOTAL_STEPS = len(STEPS)


def run_wizard(ctx: BootstrapContext) -> None:
    """Run the full bootstrap wizard pipeline."""
    completed = 0

    for step_cls in STEPS:
        step = step_cls()
        ui.step_header(step.number, step.name, completed, TOTAL_STEPS)

        try:
            if step.check(ctx):
                # Step outcome already present → skip
                completed += 1
                continue

            step.run(ctx)
            completed += 1
            ui.success(f"Step {step.number} abgeschlossen")

        except click.ClickException:
            raise
        except Exception as exc:
            ui.error(f"Fehler in Step {step.number}: {exc}")
            raise click.ClickException(str(exc))

    # All steps done — show summary
    github_url = ctx.github_url if ctx.mode == "github" else None
    ui.summary_box(
        project_name=ctx.project_name,
        project_dir=str(ctx.project_dir),
        github_url=github_url,
        domain=ctx.base_domain,
        keychain=True,
    )
