"""Step 3 (fullstack): clone template, generate keys, replace placeholders, vault."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import click

from cli import wizard_output as ui
from cli.bootstrap import (
    TEMPLATE_OWNER,
    TEMPLATE_REPO,
    TEMPLATE_VAULT_PASSWORD,
    _generate_docker_config_b64,
    _generate_ssh_keypair,
)
from cli.sync_commands import _replace_placeholders, _run_command

from ..base import WizardStep, has_placeholders, prompt_user_public_key, repo_exists
from ..context import BootstrapContext
from ..vault_guard import (
    read_keychain_password,
    store_keychain_password,
    vault_is_decryptable,
    verify_rotation,
)


class ProjectStep(WizardStep):
    number = 3
    name = "Projekt erstellen"

    def check(self, ctx: BootstrapContext) -> bool:
        if ctx.mode != "github":
            return False

        if not ctx.project_dir.exists():
            if repo_exists(ctx.full_repo):
                ui.info(
                    f"Repository {ctx.full_repo} existiert, aber ist nicht lokal geklont."
                )
                return False
            return False

        if has_placeholders(ctx.project_dir):
            ui.info(
                "Repository existiert, hat aber noch Placeholder — konfiguriere neu..."
            )
            return False

        # "Configured" is not enough — the vault must actually be sealed with the
        # password we have in the Keychain. A previous run that rotated the vault
        # but died before persisting (or vice versa) leaves a healthy-looking dir
        # with an undecryptable vault; re-run the step instead of skipping it.
        keychain_password = read_keychain_password(ctx.project_name)
        if not vault_is_decryptable(ctx.deployment_dir, keychain_password):
            ui.info(
                "Vault lässt sich nicht mit dem Keychain-Passwort entschlüsseln "
                "— konfiguriere Secrets neu..."
            )
            return False

        ui.skip_indicator(f"Projekt {ctx.project_name} bereits konfiguriert")
        return True

    def run(self, ctx: BootstrapContext) -> None:
        need_clone = not ctx.project_dir.exists()

        # 3a. Clone template locally as a fresh repo
        if need_clone:
            ui.action_start("Template klonen...")
            _run_command(
                [
                    "git", "clone", "--depth", "1",
                    f"https://github.com/{TEMPLATE_OWNER}/{TEMPLATE_REPO}.git",
                    str(ctx.project_dir),
                ],
                cwd=ctx.output_dir,
            )
            shutil.rmtree(ctx.project_dir / ".git")
            _run_command(["git", "init", "-b", "main"], cwd=ctx.project_dir)
            ui.action_done("Template geklont")

        # 3b. SSH Keys
        user_public_key = prompt_user_public_key()
        ui.action_start("CI SSH Key generieren...")
        with tempfile.TemporaryDirectory(prefix="bootstrap-ssh-") as ssh_tmp:
            ci_private_key, ci_public_key = _generate_ssh_keypair(
                ctx.project_name, Path(ssh_tmp)
            )
        ui.action_done("CI SSH Key generiert")

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
        from cli.update_vault_secrets import update_secrets as update_vault_secrets
        from cli.vault.common import generate_random_secret

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

        # Verify the end state before trusting it: the vault must decrypt with the
        # new password and must NOT decrypt with the public template constant.
        # Without this, a partial rotation silently leaves the vault sealed with
        # a well-known password while a different one lands in Keychain/CI.
        verify_rotation(ctx.deployment_dir, ctx.vault_password, TEMPLATE_VAULT_PASSWORD)
        ui.action_done("Vault-Passwort rotiert")

        # 3f. Persist the vault password immediately — it only lived in memory so
        # far, and every later step (token cleanup, repo creation, push) can fail
        # and orphan the freshly rotated vault. Keychain first, fail loud.
        ui.action_start("Vault-Passwort in Keychain speichern...")
        store_keychain_password(ctx.project_name, ctx.vault_password)
        ui.action_done("Vault-Passwort in Keychain gespeichert")

        # 3g. Remove .bak files left behind by vault encryption
        for bak in ctx.project_dir.rglob("*.bak"):
            try:
                bak.unlink()
            except OSError:
                pass

        # 3h. Token cleanup — immediately after vault encryption
        ui.action_start("Hetzner Token aufräumen...")
        from cli.hetzner.credentials import delete_token

        delete_token()
        ui.action_done("Hetzner Token aufgeräumt 🗑️")
