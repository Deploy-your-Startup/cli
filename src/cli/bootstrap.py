"""Bootstrap a new project from the Django template."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import webbrowser
from pathlib import Path

import click

from cli.sync_commands import (
    _github_owner,
    _replace_placeholders,
    _run_command,
)

TEMPLATE_OWNER = "Deploy-your-Startup"
TEMPLATE_REPO = "django-backend-template"
TEMPLATE_VAULT_PASSWORD = "ranhah-ceqZu9-fihfez"


def _prompt_or_env(
    label: str,
    env_var: str,
    *,
    required: bool = True,
    hidden: bool = True,
    signup_url: str | None = None,
    token_url: str | None = None,
) -> str:
    """Check env var first, then open browser and prompt interactively."""
    value = os.environ.get(env_var, "")
    if value:
        click.echo(f"{label}: (from ${env_var})")
        return value

    if signup_url:
        click.echo(f"\n  No account yet? Sign up at: {signup_url}")
    if token_url:
        click.echo(f"  Create an API token at: {token_url}")
        if click.confirm("  Open in browser?", default=True):
            webbrowser.open(token_url)

    if required:
        return click.prompt(label, hide_input=hidden)
    else:
        return click.prompt(label, default="", hide_input=hidden, show_default=False)


def _generate_docker_config_b64(github_username: str) -> str:
    """Generate base64-encoded Docker config JSON for ghcr.io using gh auth token."""
    result = _run_command(
        ["gh", "auth", "token"],
        cwd=Path.cwd(),
        capture_output=True,
    )
    gh_token = result.stdout.strip()
    if not gh_token:
        raise click.ClickException(
            "Could not get GitHub token via 'gh auth token'. "
            "Make sure you're logged in with 'gh auth login'."
        )

    auth_value = base64.b64encode(
        f"{github_username}:{gh_token}".encode()
    ).decode()
    docker_config = json.dumps(
        {"auths": {"ghcr.io": {"auth": auth_value}}}
    )
    return base64.b64encode(docker_config.encode()).decode()


def _generate_ssh_keypair(project_name: str, tmp_dir: Path) -> tuple[str, str]:
    """Generate an SSH keypair and return (private_key, public_key)."""
    key_path = tmp_dir / "ci_key"
    _run_command(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-C",
            f"{project_name}_ci@github-actions",
            "-f",
            str(key_path),
            "-N",
            "",
        ],
        cwd=tmp_dir,
        capture_output=True,
    )
    private_key = key_path.read_text()
    public_key = (key_path.with_suffix(".pub")).read_text().strip()
    return private_key, public_key


def bootstrap_project(
    *,
    mode: str,
    project_name: str,
    base_domain: str,
    additional_domains: str,
    github_username: str,
    docker_registry_host: str,
    postgres_version: str,
    hetzner_token: str,
    digital_ocean_token: str,
    sentry_dsn: str,
    output_dir: Path,
) -> None:
    """Bootstrap a new project from the Django template."""

    project_dir = output_dir / project_name

    if project_dir.exists():
        raise click.ClickException(
            f"Directory {project_dir} already exists. Remove it first."
        )

    # Step 1: Create the project
    click.echo(f"\n--- Step 1/7: Creating project '{project_name}' ---")

    if mode == "github":
        full_repo = f"{github_username}/{project_name}"
        click.echo(f"Creating GitHub repo {full_repo} from template ...")
        _run_command(
            [
                "gh",
                "repo",
                "create",
                full_repo,
                "--template",
                f"{TEMPLATE_OWNER}/{TEMPLATE_REPO}",
                "--private",
                "--clone",
            ],
            cwd=output_dir,
        )
    else:
        click.echo(f"Cloning template to {project_dir} ...")
        _run_command(
            [
                "git",
                "clone",
                "--depth",
                "1",
                f"https://github.com/{TEMPLATE_OWNER}/{TEMPLATE_REPO}.git",
                str(project_dir),
            ],
            cwd=output_dir,
        )
        # Remove template git history and init fresh
        shutil.rmtree(project_dir / ".git")
        _run_command(["git", "init"], cwd=project_dir)

    # Step 2: Generate SSH keys
    click.echo("\n--- Step 2/7: Generating SSH keys ---")
    with tempfile.TemporaryDirectory(prefix="bootstrap-ssh-") as ssh_tmp:
        ci_private_key, ci_public_key = _generate_ssh_keypair(
            project_name, Path(ssh_tmp)
        )

    # Generate a user SSH key placeholder — user should replace with their own
    user_public_key = ci_public_key  # Default to CI key, user can change later

    # Step 3: Replace placeholders
    click.echo("\n--- Step 3/7: Replacing placeholders ---")

    # Format additional_domains as YAML list
    if additional_domains:
        domains_list = [d.strip() for d in additional_domains.split(",") if d.strip()]
        additional_domains_yaml = "\n".join(f"  - {d}" for d in domains_list)
    else:
        additional_domains_yaml = "[]"

    replacements = {
        "§§deploy_your_startup.project_name§§": project_name,
        "§§deploy_your_startup.base_domain§§": base_domain,
        "§§deploy_your_startup.additional_domains§§": additional_domains_yaml,
        "§§deploy_your_startup.github_username§§": github_username,
        "§§deploy_your_startup.docker_registry_host§§": f"{docker_registry_host}/{github_username}",
        "§§deploy_your_startup.postgres_version§§": postgres_version,
        "§§deploy_your_startup.ci_key§§": ci_public_key,
        "§§deploy_your_startup.user_key§§": user_public_key,
    }

    _replace_placeholders(project_dir, replacements)
    click.echo(f"  Replaced {len(replacements)} placeholders.")

    # Step 4: Generate vault password and encrypt secrets
    click.echo("\n--- Step 4/7: Encrypting secrets ---")
    from cli.vault.common import generate_random_secret

    vault_password = generate_random_secret(length=48)

    # Generate Docker config for ghcr.io
    click.echo("  Generating Docker registry credentials ...")
    docker_config_b64 = _generate_docker_config_b64(github_username)

    deployment_dir = project_dir / "deployment"

    # Use the Python API directly instead of subprocess to avoid CLI arg issues
    click.echo("  Encrypting vault secrets ...")
    from cli.update_vault_secrets import update_secrets as update_vault_secrets

    field_random = ["k3s_token", "backend_db_password", "postgres_admin_password"]
    field_set = [
        ("postgres_admin_username", "admin"),
        ("digital_ocean_token", digital_ocean_token),
        ("docker_config_json_b64", docker_config_b64),
    ]
    if sentry_dsn:
        field_set.append(("backend_sentry_dsn", sentry_dsn))

    file_content = [
        ("ci_ssh_key", ci_private_key),
        ("hcloud_token_production", hetzner_token),
    ]

    success, _, pw_failed = update_vault_secrets(
        repo=str(deployment_dir),
        vault_password=TEMPLATE_VAULT_PASSWORD,
        vault_fields=field_random,
        set_field=field_set,
        set_file_content=file_content,
    )
    if not success or pw_failed:
        raise click.ClickException("Failed to encrypt vault secrets.")
    click.echo("  Secrets encrypted with template password.")

    # Step 5: Rotate vault password
    click.echo("\n--- Step 5/7: Rotating vault password ---")
    from cli.rotate_vault import rotate_vault_password as rotate_vault

    rotated = rotate_vault(
        repo=str(deployment_dir),
        old_password=TEMPLATE_VAULT_PASSWORD,
        new_password=vault_password,
        strict=True,
    )
    if not rotated:
        raise click.ClickException("Failed to rotate vault password.")
    click.echo("  Vault password rotated.")

    # Step 6: Commit and push (GitHub mode) or just commit (local mode)
    click.echo("\n--- Step 6/7: Committing changes ---")
    _run_command(["git", "add", "-A"], cwd=project_dir)
    _run_command(
        ["git", "commit", "-m", "bootstrap: configure project"],
        cwd=project_dir,
    )

    if mode == "github":
        full_repo = f"{github_username}/{project_name}"

        # Allow GitHub Actions workflows to write packages (for Docker image push)
        click.echo("  Configuring GitHub Actions permissions ...")
        _run_command(
            [
                "gh", "api", "-X", "PUT",
                f"repos/{full_repo}/actions/permissions",
                "-f", "default_workflow_permissions=write",
                "-F", "can_approve_pull_request_reviews=true",
            ],
            cwd=project_dir,
            capture_output=True,
        )

        click.echo("  Setting GitHub Actions secret VAULT_PASSWORD ...")
        _run_command(
            ["gh", "secret", "set", "VAULT_PASSWORD", "--body", vault_password],
            cwd=project_dir,
            capture_output=True,
        )
        click.echo("  Pushing to GitHub ...")
        _run_command(
            ["git", "push", "origin", "main"],
            cwd=project_dir,
            capture_output=True,
        )

    # Step 7: Summary
    click.echo("\n--- Step 7/7: Done! ---")
    click.echo("")
    click.echo("=" * 60)
    click.echo(f"  Project: {project_name}")
    click.echo(f"  Location: {project_dir}")
    if mode == "github":
        click.echo(
            f"  GitHub: https://github.com/{github_username}/{project_name}"
        )
    click.echo(f"  Domain: {base_domain}")
    click.echo("")
    click.echo(f"  Vault Password: {vault_password}")
    click.echo("  SAVE THIS PASSWORD — you need it for deployments!")
    click.echo("=" * 60)
    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  cd {project_dir}/deployment")
    click.echo(f"  ./make.sh setup_ansible")
    click.echo(
        f"  ./make.sh infrastructure --environment production --vault_password <pw>"
    )
    click.echo(f"  ./make.sh deploy --environment production --vault_password <pw>")
