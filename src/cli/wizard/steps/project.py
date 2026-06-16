"""Step 3 (fullstack): clone template, generate keys, replace placeholders, vault."""

from __future__ import annotations

import shlex
import shutil
import tempfile
import textwrap
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


BYOS_CI_WORKFLOWS = [
    "build-and-deploy-backend.yml",
    "deploy-infrastructure.yml",
    "deploy.yml",
]
BYOS_DEPLOY_PUBLIC_KEY_FILE = "byos_deploy_key.pub"
BYOS_DEPLOY_PUBLIC_KEY_IGNORE = f"deployment/{BYOS_DEPLOY_PUBLIC_KEY_FILE}"


def write_byos_ci_workflows(project_dir: Path, github_username: str) -> list[str]:
    """Write BYOS-compatible project-local GitHub Actions workflows."""
    workflows_dir = project_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    workflows = {
        "deploy.yml": """
            name: Deploy

            on:
              push:
                branches:
                  - main
                paths:
                  - 'deployment/**'
              workflow_dispatch:
                inputs:
                  environment:
                    description: Environment which should be deployed
                    required: true
                    default: production
                    type: choice
                    options:
                      - production

            concurrency:
              group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event.inputs.environment || 'production' }}
              cancel-in-progress: true

            jobs:
              deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  packages: read
                steps:
                  - uses: actions/checkout@v6

                  - name: Export shared Ansible roles
                    uses: __GITHUB_USERNAME__/deploy-your-startup/.github/actions/export-shared-roles@main
                    with:
                      destination: deployment/.shared-roles

                  - uses: astral-sh/setup-uv@v7
                    with:
                      working-directory: deployment
                      python-version: "3.14"
                      enable-cache: true
                      cache-dependency-glob: |
                        pyproject.toml
                        uv.lock

                  - name: Deploy
                    env:
                      VAULT_PASSWORD: ${{ secrets.VAULT_PASSWORD }}
                      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
                    run: |
                      uv run --project deployment startup ansible deploy \\
                        --working-directory deployment \\
                        --environment "${{ github.event.inputs.environment || 'production' }}" \\
                        --vault-password "$VAULT_PASSWORD" \\
                        --no-refresh
        """,
        "deploy-infrastructure.yml": """
            name: Deploy Infrastructure

            on:
              workflow_dispatch:
                inputs:
                  environment:
                    description: Environment which infrastructure should be deployed
                    required: true
                    default: production
                    type: choice
                    options:
                      - production

            concurrency:
              group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event.inputs.environment || 'production' }}
              cancel-in-progress: true

            jobs:
              deploy-infrastructure:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  packages: read
                steps:
                  - uses: actions/checkout@v6

                  - name: Export shared Ansible roles
                    uses: __GITHUB_USERNAME__/deploy-your-startup/.github/actions/export-shared-roles@main
                    with:
                      destination: deployment/.shared-roles

                  - uses: astral-sh/setup-uv@v7
                    with:
                      working-directory: deployment
                      python-version: "3.14"
                      enable-cache: true
                      cache-dependency-glob: |
                        pyproject.toml
                        uv.lock

                  - name: Deploy infrastructure
                    env:
                      VAULT_PASSWORD: ${{ secrets.VAULT_PASSWORD }}
                      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
                    run: |
                      uv run --project deployment startup ansible infrastructure \\
                        --working-directory deployment \\
                        --environment "${{ github.event.inputs.environment || 'production' }}" \\
                        --vault-password "$VAULT_PASSWORD" \\
                        --no-refresh
        """,
        "build-and-deploy-backend.yml": """
            name: Build and Deploy Backend

            on:
              push:
                branches:
                  - main
                paths:
                  - 'backend/**'
              workflow_dispatch:
                inputs:
                  environment:
                    description: Environment to deploy
                    required: true
                    default: production
                    type: choice
                    options:
                      - production

            concurrency:
              group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event.inputs.environment || 'production' }}
              cancel-in-progress: true

            env:
              REGISTRY: ghcr.io
              IMAGE_NAME: ${{ github.repository }}-backend
              DOCKER_IMAGE_TAG: ${{ github.run_number }}

            jobs:
              build-and-deploy:
                runs-on: ubuntu-latest
                permissions:
                  contents: read
                  packages: write
                services:
                  postgres:
                    image: postgres:latest
                    env:
                      POSTGRES_DB: test_db
                      POSTGRES_PASSWORD: pa55w0rt
                      POSTGRES_USER: postgres
                    ports:
                      - 5432:5432
                    options: >-
                      --health-cmd pg_isready
                      --health-interval 10s
                      --health-timeout 5s
                      --health-retries 5
                steps:
                  - uses: actions/checkout@v6

                  - name: Export shared Ansible roles
                    uses: __GITHUB_USERNAME__/deploy-your-startup/.github/actions/export-shared-roles@main
                    with:
                      destination: deployment/.shared-roles

                  - uses: docker/setup-buildx-action@v4

                  - uses: docker/login-action@v4
                    with:
                      registry: ${{ env.REGISTRY }}
                      username: ${{ github.actor }}
                      password: ${{ secrets.GITHUB_TOKEN }}

                  - name: Build test image
                    uses: docker/build-push-action@v7
                    with:
                      context: ./backend
                      file: ./backend/Dockerfile
                      target: test
                      tags: test-image:${{ env.DOCKER_IMAGE_TAG }}
                      push: false
                      load: true
                      cache-from: type=gha
                      cache-to: type=gha,mode=max

                  - name: Run backend tests
                    run: |
                      docker run --network host \\
                        -e DATABASE_URL="postgres://postgres:pa55w0rt@localhost:5432/test_db" \\
                        --rm test-image:${{ env.DOCKER_IMAGE_TAG }} ./make.sh test

                  - name: Build and push backend image
                    uses: docker/build-push-action@v7
                    with:
                      context: ./backend
                      file: ./backend/Dockerfile
                      push: true
                      tags: |
                        ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
                        ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ env.DOCKER_IMAGE_TAG }}
                      cache-from: type=gha
                      cache-to: type=gha,mode=max
                      platforms: linux/amd64

                  - uses: astral-sh/setup-uv@v7
                    with:
                      working-directory: deployment
                      python-version: "3.14"
                      enable-cache: true
                      cache-dependency-glob: |
                        pyproject.toml
                        uv.lock

                  - name: Deploy backend
                    env:
                      VAULT_PASSWORD: ${{ secrets.VAULT_PASSWORD }}
                      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
                    run: |
                      uv run --project deployment startup ansible deploy \\
                        --working-directory deployment \\
                        --environment "${{ github.event.inputs.environment || 'production' }}" \\
                        --service backend \\
                        --vault-password "$VAULT_PASSWORD" \\
                        --no-refresh
        """,
    }

    written: list[str] = []
    for workflow_name in BYOS_CI_WORKFLOWS:
        workflow = textwrap.dedent(workflows[workflow_name]).lstrip()
        (workflows_dir / workflow_name).write_text(
            workflow.replace("__GITHUB_USERNAME__", github_username)
        )
        written.append(workflow_name)
    return written


def write_byos_deploy_public_key(deployment_dir: Path, public_key: str) -> Path:
    """Write the public deploy key users must add to their existing VPS."""
    key_path = deployment_dir / BYOS_DEPLOY_PUBLIC_KEY_FILE
    key_path.write_text(public_key.strip() + "\n")
    return key_path


def ensure_byos_deploy_public_key_ignored(project_dir: Path) -> None:
    """Keep the generated BYOS public key helper file out of git."""
    gitignore_path = project_dir / ".gitignore"
    existing = gitignore_path.read_text().splitlines() if gitignore_path.exists() else []
    if BYOS_DEPLOY_PUBLIC_KEY_IGNORE not in existing:
        with gitignore_path.open("a") as gitignore:
            if existing and existing[-1].strip():
                gitignore.write("\n")
            gitignore.write(BYOS_DEPLOY_PUBLIC_KEY_IGNORE + "\n")


def byos_deploy_key_install_command(ctx: BootstrapContext, public_key: str) -> str:
    """Return the command that installs the deploy public key on the BYOS VPS."""
    user_host = f"{ctx.byos_ssh_user}@{ctx.byos_host}"
    remote = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    )
    return (
        f"printf '%s\\n' {shlex.quote(public_key.strip())} "
        f"| ssh {shlex.quote(user_host)} {shlex.quote(remote)}"
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

        ctx.vault_password = keychain_password
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

        # ci_ssh_key doubles as the deploy key for byos (the local/CI deploy reads
        # it from the vault to SSH into the VPS). The hcloud token only exists for
        # the Hetzner provider.
        file_content = [("ci_ssh_key", ci_private_key)]
        if ctx.provider == "hetzner":
            file_content.append(("hcloud_token_production", ctx.hetzner_token))

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

        # 3h. Provider-specific finishing touches
        if ctx.provider == "byos":
            # Write the static inventory and hand the user the deploy public key.
            from .byos import write_byos_inventory

            ui.action_start("BYOS-Inventory schreiben...")
            inv_path = write_byos_inventory(ctx.deployment_dir, ctx)
            ui.action_done(f"Inventory geschrieben: {inv_path.name}")

            ui.action_start("BYOS-Deploy-Key schreiben...")
            deploy_key_path = write_byos_deploy_public_key(
                ctx.deployment_dir, ci_public_key
            )
            ensure_byos_deploy_public_key_ignored(ctx.project_dir)
            ui.action_done(f"Deploy-Key geschrieben: {deploy_key_path.name}")

            ui.action_start("BYOS-CI-Workflows schreiben...")
            written_workflows = write_byos_ci_workflows(
                ctx.project_dir, ctx.github_username
            )
            ui.action_done("Workflows geschrieben: " + ", ".join(written_workflows))

            authorized_keys = (
                "/root/.ssh/authorized_keys"
                if ctx.byos_ssh_user == "root"
                else f"/home/{ctx.byos_ssh_user}/.ssh/authorized_keys"
            )
            ui.info(
                "Füge diesen Deploy-Public-Key auf dem Server in "
                f"{authorized_keys} ein, damit Ansible sich einloggen kann:\n\n"
                f"{ci_public_key}\n"
                "Schnellweg:\n"
                f"  {byos_deploy_key_install_command(ctx, ci_public_key)}\n"
                f"Die lokale Hilfsdatei {deploy_key_path} ist in .gitignore "
                "eingetragen und wird nicht committed."
            )
        else:
            # Token cleanup — immediately after vault encryption.
            ui.action_start("Hetzner Token aufräumen...")
            from cli.hetzner.credentials import delete_token

            delete_token()
            ui.action_done("Hetzner Token aufgeräumt 🗑️")
