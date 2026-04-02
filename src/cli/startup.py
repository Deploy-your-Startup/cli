#!/usr/bin/env python3
"""
Startup CLI - Command line tool for Deploy Your Startup operations
"""

import subprocess
import sys
from pathlib import Path
import click


def run_command(cmd, verbose=False):
    """Run a command with proper error handling"""
    if verbose:
        click.echo(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=True, capture_output=not verbose)
        if not verbose and result.stdout:
            click.echo(result.stdout.decode("utf-8"))
        return 0
    except subprocess.CalledProcessError as e:
        click.echo(f"Error: {e}", err=True)
        if not verbose and e.stdout:
            click.echo(e.stdout.decode("utf-8"))
        if e.stderr:
            click.echo(e.stderr.decode("utf-8"), err=True)
        return e.returncode


def get_python_cmd():
    """Get the Python command to use with uv run"""
    return ["uv", "run", "python"]


@click.group()
def cli():
    """Startup CLI - Command line tool for Deploy Your Startup operations"""
    pass


# === BOOTSTRAP COMMAND ===
@cli.command("bootstrap")
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def bootstrap(verbose):
    """Bootstrap a new startup — guided wizard.

    Walks you through the complete setup: Domain, Hetzner project/token,
    project creation from template, and finalization (commit, push, Keychain).

    Each step checks if its outcome already exists, so you can safely
    re-run after an interruption.
    """
    import re
    from cli import wizard_output as ui
    from cli.bootstrap_wizard import BootstrapContext, run_wizard
    from cli.sync_commands import _github_owner

    ui.banner()

    # ── Collect inputs ───────────────────────────────────────────

    # Project name (kebab-case validated)
    while True:
        project_name = ui.text_input("Projektname (kebab-case)")
        if re.match(r"^[a-z][a-z0-9-]*$", project_name):
            break
        ui.error("Bitte kebab-case verwenden (z.B. mein-startup).")

    # Domain
    base_domain = ui.text_input("Domain (z.B. mein-startup.de)")

    # Optional: additional domains
    additional_domains = ui.text_input(
        "Weitere Domains (komma-getrennt, Enter zum Überspringen)",
        default="",
        show_default=False,
    )

    # Optional: Sentry DSN
    import os

    sentry_dsn = os.environ.get("SENTRY_DSN", "")
    if not sentry_dsn:
        sentry_dsn = ui.text_input(
            "Sentry DSN (optional, Enter zum Überspringen)",
            default="",
            show_default=False,
        )

    # Auto-detect GitHub username
    try:
        github_username = _github_owner(None)
        ui.info(f"GitHub User: {github_username}")
    except Exception:
        github_username = ui.text_input("GitHub username/org")

    # Output directory
    output_dir = ui.text_input(
        "Output-Verzeichnis",
        default=str(Path.home() / "Projects"),
    )

    # ── Summary + confirmation ───────────────────────────────────

    ui.input_summary(
        {
            "Projekt": project_name,
            "Domain": base_domain,
            "GitHub": f"{github_username}/{project_name}",
            "Registry": f"ghcr.io/{github_username}",
            "Postgres": "17",
        }
    )

    if not ui.confirm("Passt das?", default=True):
        raise SystemExit(0)

    # ── Run wizard ───────────────────────────────────────────────

    ctx = BootstrapContext(
        project_name=project_name,
        base_domain=base_domain,
        additional_domains=additional_domains,
        github_username=github_username,
        postgres_version="17",
        sentry_dsn=sentry_dsn,
        output_dir=Path(output_dir),
    )

    run_wizard(ctx)


@cli.group()
def secrets():
    """Manage vault secrets"""
    pass


@secrets.command("update")
@click.option(
    "--vault-password",
    "-p",
    required=True,
    help="Vault password for encryption/decryption",
)
@click.option(
    "--repo",
    "-r",
    required=True,
    help="Repository path, directory, or specific YAML file to update",
)
@click.option(
    "--dry-run",
    "-d",
    is_flag=True,
    help="Preview changes without applying them (writes to dry-run-output/)",
)
@click.option(
    "--only-existing",
    "-e",
    is_flag=True,
    help="Only update fields that already have vault blocks",
)
@click.option(
    "--verify-password",
    is_flag=True,
    help="Verify vault password can decrypt existing secrets before updating",
)
# New clearer parameter names
@click.option(
    "--field-random",
    "-fr",
    multiple=True,
    help="Generate random value for inline vault field (can be repeated)",
)
@click.option(
    "--field-set",
    "-fs",
    multiple=True,
    nargs=2,
    help="Set specific value for inline vault field: FIELD VALUE (can be repeated)",
)
@click.option(
    "--file-rotate",
    "-rot",
    multiple=True,
    help="Re-encrypt full vault file with same password (can be repeated)",
)
@click.option(
    "--file-content",
    "-fc",
    multiple=True,
    nargs=2,
    help="Replace content of full vault file: FILE CONTENT (can be repeated)",
)
# Backward compatibility - keep old names as hidden aliases
@click.option(
    "--vault-field",
    "-f",
    multiple=True,
    hidden=True,
    help="[DEPRECATED: use --field-random] Field to generate random value for",
)
@click.option(
    "--vault-file",
    "--vf",
    multiple=True,
    hidden=True,
    help="[DEPRECATED: use --file-rotate] Vault file to rotate",
)
@click.option(
    "--set-field",
    "-s",
    multiple=True,
    nargs=2,
    hidden=True,
    help="[DEPRECATED: use --field-set] Set specific field to a value",
)
@click.option(
    "--set-file-content",
    "--sf",
    multiple=True,
    nargs=2,
    hidden=True,
    help="[DEPRECATED: use --file-content] Set specific file content",
)
@click.option(
    "--verbose",
    "-V",
    is_flag=True,
    help="Show detailed output including file paths and operations",
)
@click.option(
    "--secret-length",
    "-l",
    type=int,
    default=32,
    help="Length for randomly generated secrets (default: 32)",
)
def update_secrets(
    vault_password,
    repo,
    dry_run,
    only_existing,
    verify_password,
    field_random,
    field_set,
    file_rotate,
    file_content,
    vault_field,
    vault_file,
    set_field,
    set_file_content,
    verbose,
    secret_length,
):
    """
    Update vault secrets in Ansible YAML files.
    
    This command supports two types of operations:
    
    \b
    1. INLINE FIELD UPDATES: Update specific fields in YAML files that contain
       inline vault blocks (field: !vault | ...). Use --field-random to generate
       a new random value or --field-set to set a specific value.
    
    \b
    2. FULL FILE OPERATIONS: Work with completely encrypted vault files.
       Use --file-rotate to re-encrypt with the same password (changes salt)
       or --file-content to replace the entire file content.
    
    \b
    Examples:
    
    \b
      # Generate random value for a field
      startup secrets update -r . -p PASSWORD --field-random backend_db_password
      
    \b
      # Set specific value for a field
      startup secrets update -r . -p PASSWORD --field-set api_key "my-key"
      
    \b
      # Update multiple fields at once
      startup secrets update -r . -p PASSWORD \\
        --field-random db_password \\
        --field-set api_key "my-key"
      
    \b
      # Rotate an encrypted file (re-encrypt with same password)
      startup secrets update -r . -p PASSWORD --file-rotate secrets.yml
      
    \b
      # Update a specific file directly
      startup secrets update -r path/to/file.yml -p PASSWORD --field-random db_pass
      
    \b
      # Preview changes without applying (dry run)
      startup secrets update -r . -p PASSWORD --field-random db_pass --dry-run
    """
    from cli.update_vault_secrets import update_secrets as update_vault_secrets

    # Merge new and old parameter names for backward compatibility
    # Prefer new names if both are provided
    merged_field_random = list(field_random) if field_random else []
    if vault_field:  # Old parameter name
        merged_field_random.extend(vault_field)

    merged_field_set = list(field_set) if field_set else []
    if set_field:  # Old parameter name
        merged_field_set.extend(set_field)

    merged_file_rotate = list(file_rotate) if file_rotate else []
    if vault_file:  # Old parameter name
        merged_file_rotate.extend(vault_file)

    merged_file_content = list(file_content) if file_content else []
    if set_file_content:  # Old parameter name
        merged_file_content.extend(set_file_content)

    # Convert to appropriate formats
    set_field_pairs = merged_field_set if merged_field_set else None
    set_file_content_pairs = merged_file_content if merged_file_content else None

    # Call the update_secrets function directly
    success, updated, password_verification_failed = update_vault_secrets(
        repo=repo,
        vault_password=vault_password,
        vault_fields=merged_field_random if merged_field_random else None,
        vault_files=merged_file_rotate if merged_file_rotate else None,
        secret_length=secret_length,
        dry_run=dry_run,
        verbose=verbose,
        only_existing=only_existing,
        verify_password=verify_password,
        set_field=set_field_pairs,
        set_file_content=set_file_content_pairs,
    )

    # Return appropriate exit code based on the result
    if password_verification_failed:
        return 1
    elif not success:
        return 2
    return 0


@secrets.command("rotate-password")
@click.option("--repo", "-r", required=True, help="Repository path")
@click.option("--old-password", required=True, help="Old vault password")
@click.option("--new-password", required=True, help="New vault password")
@click.option("--file", "-f", help="Specific file to rotate (optional)")
@click.option("--dry-run", "-d", is_flag=True, help="Don't write changes, only report")
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
@click.option(
    "--strict",
    "-s",
    is_flag=True,
    help="Abort if any vault files can't be decrypted with the old password",
)
def rotate_vault_password(
    repo, old_password, new_password, file, dry_run, verbose, strict
):
    """Rotate vault password in a repository"""
    from cli.rotate_vault import rotate_vault_password as rotate_vault

    # Call the rotate_vault_password function directly
    rotated_files = rotate_vault(
        repo=repo,
        old_password=old_password,
        new_password=new_password,
        file_path=file,
        dry_run=dry_run,
        verbose=verbose,
        strict=strict,
    )

    if rotated_files:
        return 0
    return 1


@secrets.command("list-vaults")
@click.option("--repo", "-r", default=".", help="Repository path")
@click.option("--file", "-f", help="Specific file to check (optional)")
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def list_vault_files(repo, file, verbose):
    """List files with vaulted content"""
    from cli.rotate_vault import list_status

    # Call the list_status function directly
    vaulted_files = list_status(repo=repo, file_path=file, verbose=verbose)

    if vaulted_files:
        return 0
    return 1


@secrets.command("get-field")
@click.option(
    "--file", "-f", required=True, help="Path to the file containing the vault field"
)
@click.option("--field", required=True, help="Name of the vault field to retrieve")
@click.option(
    "--vault-password", "-p", required=True, help="Vault password for decryption"
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def get_vault_field(file, field, vault_password, verbose):
    """Get the decrypted value of a vault field from a file"""
    from cli.vault.fields import get_inline_vault_value

    file_path = Path(file)
    if not file_path.exists():
        click.echo(f"Error: File {file} does not exist", err=True)
        return 1

    if verbose:
        click.echo(f"Getting value for field {field} from file {file}")

    value = get_inline_vault_value(file_path, field, vault_password, verbose)
    if value is None:
        click.echo(f"Error: Could not retrieve value for field {field}", err=True)
        return 1

    click.echo(value)
    return 0


@secrets.command("update-inline-field")
@click.option(
    "--file", "-f", required=True, help="Path to the file containing the vault field"
)
@click.option("--field", required=True, help="Name of the vault field to update")
@click.option("--value", required=True, help="New value for the field")
@click.option(
    "--vault-password", "-p", required=True, help="Vault password for encryption"
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def update_inline_vault_field_cmd(file, field, value, vault_password, verbose):
    """Update the value of an inline vault field in a file"""
    from cli.vault.fields import update_inline_vault_field

    file_path = Path(file)
    if not file_path.exists():
        click.echo(f"Error: File {file} does not exist", err=True)
        return 1

    if verbose:
        click.echo(f"Updating value for field {field} in file {file}")

    success = update_inline_vault_field(file_path, field, value, vault_password)
    if not success:
        click.echo(f"Error: Could not update value for field {field}", err=True)
        return 1

    click.echo(f"Successfully updated value for {field}")
    return 0


# === DEPLOY COMMANDS ===
@cli.group()
def deploy():
    """Deployment operations"""
    pass


@cli.group(invoke_without_command=True)
@click.option(
    "--owner", default=None, help="Target GitHub owner (defaults to gh auth user)"
)
@click.option(
    "--repo-name",
    default="deploy-your-startup",
    show_default=True,
    help="Target shared deploy repository name",
)
@click.option(
    "--source-owner",
    default="Deploy-your-Startup",
    show_default=True,
    help="Shared deploy template owner",
)
@click.option(
    "--source-repo",
    default="deploy-template",
    show_default=True,
    help="Shared deploy template repository",
)
@click.option("--private/--public", default=True, show_default=True)
@click.option("--dry-run", is_flag=True, help="Preview sync without commit/push")
@click.pass_context
def sync(ctx, owner, repo_name, source_owner, source_repo, private, dry_run):
    """Sync the shared deploy template into your GitHub account."""
    if ctx.invoked_subcommand is None:
        from cli.sync_commands import sync_deploy_repo

        sync_deploy_repo(
            owner=owner,
            repo_name=repo_name,
            source_owner=source_owner,
            source_repo=source_repo,
            private=private,
            dry_run=dry_run,
        )


@cli.group()
def ansible():
    """Shared Ansible deployment operations"""
    pass


@deploy.command("create")
@click.option("--repo-name", required=True, help="Name of the repository to create")
@click.option(
    "--repo-description",
    default="Created by DeployYourStartup.com",
    help="Description of the repository",
)
@click.option(
    "--repo-private/--repo-public",
    default=True,
    help="Whether the repository should be private",
)
@click.option(
    "--template-owner", default="Deploy-your-Startup", help="Template repository owner"
)
@click.option(
    "--template-repo",
    default="django-backend-template",
    help="Template repository name",
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def create_deployment(
    repo_name, repo_description, repo_private, template_owner, template_repo, verbose
):
    """Deploy an application from a GitHub template"""
    from cli.deploy import deploy_github_repo

    return deploy_github_repo(
        repo_name=repo_name,
        repo_description=repo_description,
        repo_private=repo_private,
        template_owner=template_owner,
        template_repo=template_repo,
        verbose=verbose,
    )


@deploy.command("github")
@click.option("--repo-name", required=True, help="Name of the repository to create")
@click.option(
    "--repo-description",
    default="Created by DeployYourStartup.com",
    help="Description of the repository",
)
@click.option(
    "--repo-private/--repo-public",
    default=True,
    help="Whether the repository should be private",
)
@click.option(
    "--template-owner",
    default="Deploy-your-Startup",
    help="Owner of the template repository",
)
@click.option(
    "--template-repo",
    default="django-backend-template",
    help="Name of the template repository",
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def github_deploy(
    repo_name, repo_description, repo_private, template_owner, template_repo, verbose
):
    """Deploy a GitHub repository from a template"""
    from cli.deploy import deploy_github_repo

    return deploy_github_repo(
        repo_name=repo_name,
        repo_description=repo_description,
        repo_private=repo_private,
        template_owner=template_owner,
        template_repo=template_repo,
        verbose=verbose,
    )


@ansible.command("setup_ansible")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_setup_ansible(working_directory, shared_dir, version, refresh, repo_url):
    """Prepare shared roles and install Ansible collections."""
    from cli.ansible_commands import setup_ansible

    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("setup")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_setup(working_directory, shared_dir, version, refresh, repo_url):
    """Install deployment dependencies and prepare shared Ansible roles."""
    from cli.ansible_commands import setup

    setup(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("deploy")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option(
    "--vault-password-from-keychain",
    is_flag=True,
    help="Force reading the vault password from the macOS Keychain using the current project name",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--service", default="all", show_default=True, help="Service tag to deploy"
)
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_deploy(
    vault_password,
    vault_password_from_keychain,
    environment,
    service,
    working_directory,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Deploy services via Ansible playbook."""
    from cli.ansible_commands import resolve_vault_password, run_deploy

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        vault_password_from_keychain=vault_password_from_keychain,
        working_directory=working_directory,
    )

    run_deploy(
        vault_password=resolved_vault_password,
        environment=environment,
        service=service,
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("infrastructure")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option(
    "--vault-password-from-keychain",
    is_flag=True,
    help="Force reading the vault password from the macOS Keychain using the current project name",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_infrastructure(
    vault_password,
    vault_password_from_keychain,
    environment,
    working_directory,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Provision infrastructure via Ansible playbook."""
    from cli.ansible_commands import resolve_vault_password, run_infrastructure

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        vault_password_from_keychain=vault_password_from_keychain,
        working_directory=working_directory,
    )

    run_infrastructure(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("kubeconfig")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option(
    "--vault-password-from-keychain",
    is_flag=True,
    help="Force reading the vault password from the macOS Keychain using the current project name",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option("--inventory", default="inventory.hcloud.yml", show_default=True)
@click.option("--out", default=None, help="Output path for kubeconfig")
@click.option("--ssh-user", "--ssh_user", default="root", show_default=True)
@click.option(
    "--master-host", "--master_host", default=None, help="Override detected master host"
)
@click.option(
    "--context-name",
    "--context_name",
    default=None,
    help="Override derived kube context name",
)
@click.option("--env-suffix/--no-env-suffix", default=True, show_default=True)
@click.option("--make-current/--no-make-current", default=True, show_default=True)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_kubeconfig(
    vault_password,
    vault_password_from_keychain,
    environment,
    working_directory,
    inventory,
    out,
    ssh_user,
    master_host,
    context_name,
    env_suffix,
    make_current,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Fetch and merge the cluster kubeconfig."""
    from cli.ansible_commands import resolve_vault_password, run_kubeconfig

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        vault_password_from_keychain=vault_password_from_keychain,
        working_directory=working_directory,
    )

    run_kubeconfig(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        inventory=inventory,
        out=out,
        ssh_user=ssh_user,
        master_host=master_host,
        context_name=context_name,
        env_suffix=env_suffix,
        make_current=make_current,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("backup")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option(
    "--vault-password-from-keychain",
    is_flag=True,
    help="Force reading the vault password from the macOS Keychain using the current project name",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--backup-dir", "--backup_dir", default=None, help="Override backup directory"
)
@click.option("--playbook", default="backup-playbook.yml", show_default=True)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_backup(
    vault_password,
    vault_password_from_keychain,
    environment,
    working_directory,
    backup_dir,
    playbook,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Run a backup playbook."""
    from cli.ansible_commands import resolve_vault_password, run_backup

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        vault_password_from_keychain=vault_password_from_keychain,
        working_directory=working_directory,
    )

    run_backup(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        backup_dir=backup_dir,
        playbook=playbook,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("update-vms")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option(
    "--vault-password-from-keychain",
    is_flag=True,
    help="Force reading the vault password from the macOS Keychain using the current project name",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--playbook",
    default="update-vms-playbook.yml",
    show_default=True,
    help="Playbook that updates VM packages",
)
@click.option(
    "--limit",
    default=None,
    help="Optional extra Ansible limit expression within the environment",
)
@click.option(
    "--reboot/--no-reboot",
    default=False,
    show_default=True,
    help="Reboot hosts after package upgrades when required",
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_update_vms(
    vault_password,
    vault_password_from_keychain,
    environment,
    working_directory,
    playbook,
    limit,
    reboot,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Update Hetzner VM packages via Ansible playbook."""
    from cli.ansible_commands import resolve_vault_password, run_update_vms

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        vault_password_from_keychain=vault_password_from_keychain,
        working_directory=working_directory,
    )

    run_update_vms(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        playbook=playbook,
        limit=limit,
        reboot=reboot,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("restore")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option(
    "--vault-password-from-keychain",
    is_flag=True,
    help="Read the vault password from the macOS Keychain using the current project name",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--backup-dir",
    "--backup_dir",
    default=None,
    help="Directory containing backup artifacts (defaults to ~/Backups/<project>)",
)
@click.option(
    "--db-file", "--db_file", default=None, help="Specific database dump to restore"
)
@click.option(
    "--media-file",
    "--media_file",
    default=None,
    help="Specific media archive to restore",
)
@click.option("--playbook", default="restore-playbook.yml", show_default=True)
@click.option(
    "--restore-db/--no-restore-db",
    default=True,
    show_default=True,
    help="Restore the PostgreSQL database",
)
@click.option(
    "--restore-media/--no-restore-media",
    default=True,
    show_default=True,
    help="Restore the media volume",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Confirm destructive restore into the selected environment",
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_restore(
    vault_password,
    vault_password_from_keychain,
    environment,
    working_directory,
    backup_dir,
    db_file,
    media_file,
    playbook,
    restore_db,
    restore_media,
    yes,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Run a restore playbook."""
    from cli.ansible_commands import resolve_vault_password, run_restore

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        vault_password_from_keychain=vault_password_from_keychain,
        working_directory=working_directory,
    )

    run_restore(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        backup_dir=backup_dir,
        db_file=db_file,
        media_file=media_file,
        playbook=playbook,
        restore_db=restore_db,
        restore_media=restore_media,
        confirm=yes,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


# === HETZNER COMMANDS ===
@cli.group()
def hetzner():
    """Hetzner Cloud account, project, domain & token management via browser automation."""
    pass


@hetzner.command("setup")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.option(
    "--project", "-p", default=None, help="Project name (prompted if not given)"
)
@click.option(
    "--token-name",
    "--token_name",
    "token_name",
    default="deploy-cli",
    help="Name for the API token",
)
@click.option(
    "--register",
    is_flag=True,
    default=False,
    help="Register a new account instead of logging in",
)
@click.option("--email", default=None, help="Email for account registration")
def hetzner_setup(headless, project, token_name, register, email):
    """Full interactive setup: Login/Register -> Project -> API Token."""
    from cli.hetzner import get_or_create_token

    if not project:
        project = click.prompt("  Project name")

    token = get_or_create_token(
        project_name=project,
        token_name=token_name,
        headless=headless,
        register=register,
        email=email,
    )

    if token:
        click.echo(f"\n  Token: {token[:8]}...{token[-4:]}")
        click.echo(f"  Use with: startup bootstrap --hetzner-token <token>")
    else:
        click.echo("\n  No token obtained.", err=True)
        raise SystemExit(1)


@hetzner.command("domain")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.argument("domain", required=False)
def hetzner_domain(headless, domain):
    """Register a domain via Hetzner Robot."""
    from cli.hetzner import register_domain as do_register

    if not domain:
        domain = click.prompt("  Domain to register (e.g. example.com)")

    ok = do_register(domain=domain, headless=headless)
    if not ok:
        raise SystemExit(1)


@hetzner.command("token")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.option(
    "--token-name",
    "--token_name",
    "token_name",
    default="deploy-cli",
    help="Name for the API token",
)
def hetzner_token(headless, token_name):
    """Create a new API token only (requires existing login session)."""
    from cli.hetzner import get_or_create_token

    project = click.prompt("  Project name (for reference)")

    token = get_or_create_token(
        project_name=project,
        token_name=token_name,
        headless=headless,
    )

    if token:
        click.echo(f"\n  Token: {token[:8]}...{token[-4:]}")
    else:
        click.echo("\n  No token obtained.", err=True)
        raise SystemExit(1)


@hetzner.command("status")
def hetzner_status():
    """Show stored token information."""
    from cli.hetzner.credentials import show_token_info

    show_token_info()


@hetzner.command("clean")
def hetzner_clean():
    """Remove stored credentials and browser state."""
    import shutil
    from cli.hetzner.config import CONFIG_DIR

    if click.confirm(f"  Delete all stored data in {CONFIG_DIR}?", default=False):
        if CONFIG_DIR.exists():
            shutil.rmtree(CONFIG_DIR)
            click.echo(f"  Deleted: {CONFIG_DIR}")
        else:
            click.echo("  Nothing to delete.")


def main():
    """Main entry point for the CLI"""
    return cli(prog_name="startup")


if __name__ == "__main__":
    sys.exit(cli())
