"""``startup secrets``: read, update, copy and rotate Ansible Vault secrets."""

import sys
from pathlib import Path

import click


def _password_scope(repo: str) -> str:
    """The directory whose name identifies the project to the password backend.

    A vault lives at ``<project>/deployment/group_vars/...``, so --repo may point
    anywhere from the project root down to a single file. Walk up to the
    ``deployment`` boundary, which is what the ansible commands always pass, so
    that ``-r group_vars/all.yml`` asks for the project's password rather than
    for one called VAULT_PASSWORD_GROUP_VARS.

    Falls back to the directory itself when there is no such ancestor, which
    keeps repositories laid out differently working as before.
    """
    path = Path(repo)
    if not path.exists():
        return repo
    path = path.resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if candidate.name == "deployment":
            return str(candidate)
    return str(path)


@click.group()
def secrets():
    """Manage vault secrets"""


@secrets.command("update")
@click.option(
    "--vault-password",
    "-p",
    default=None,
    help=(
        "Vault password. Omit it to read the password from the configured "
        "backend (`startup config`, default: macOS Keychain), the way `startup ansible` does."
    ),
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
    "--field-stdin",
    "-fi",
    default=None,
    help=(
        "Set an inline vault field to a value read from stdin. Keeps the "
        "secret out of the process arguments, where --field-set leaves it "
        "readable to anyone who can run `ps`. One field per invocation."
    ),
)
@click.option(
    "--file-stdin",
    default=None,
    help="Replace a full encrypted file with exact UTF-8 content read from stdin.",
)
@click.option(
    "--create-in",
    default=None,
    type=click.Path(),
    help=(
        "YAML file to append fields to when they have no vault block yet. "
        "Without it, a field that does not exist is an error rather than a "
        "silent no-op."
    ),
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
    field_stdin,
    file_stdin,
    create_in,
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
    from cli.ansible_commands import resolve_vault_password
    from cli.update_vault_secrets import update_secrets as update_vault_secrets

    if field_stdin and file_stdin:
        raise click.UsageError("Use either --field-stdin or --file-stdin, not both.")
    # Same resolution `startup ansible` uses: an explicit --vault-password wins,
    # otherwise the configured backend (Keychain or 1Password) is asked, keyed
    # by the project the path belongs to, so the password stays out of `ps` and
    # the shell history.
    vault_password = resolve_vault_password(vault_password, _password_scope(repo))

    # The hidden pre-rename options (--vault-field, --set-field, ...) still work
    # and add to their new counterparts.
    field_values = [*field_set, *set_field]
    if field_stdin:
        # Strip only the trailing newline a shell or an editor adds, so a
        # multi-line credential survives.
        value = _non_empty(
            sys.stdin.read().removesuffix("\n"), f"--field-stdin {field_stdin}"
        )
        field_values.append((field_stdin, value))
    file_contents = [*file_content, *set_file_content]
    if file_stdin:
        file_contents.append((file_stdin, _non_empty(sys.stdin.read(), "--file-stdin")))

    success, _updated, password_verification_failed = update_vault_secrets(
        repo=repo,
        vault_password=vault_password,
        vault_fields=[*field_random, *vault_field] or None,
        vault_files=[*file_rotate, *vault_file] or None,
        secret_length=secret_length,
        dry_run=dry_run,
        verbose=verbose,
        only_existing=only_existing,
        verify_password=verify_password,
        set_field=field_values or None,
        set_file_content=file_contents or None,
        create_in=create_in,
    )
    if password_verification_failed:
        raise click.exceptions.Exit(1)
    if not success:
        raise click.exceptions.Exit(2)
    return 0


def _non_empty(stdin_value: str, option: str) -> str:
    """A secret read from stdin, which keeps it out of the process arguments."""
    if not stdin_value:
        raise click.ClickException(f"{option} was given but stdin was empty.")
    return stdin_value


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
    raise click.exceptions.Exit(1)


@secrets.command("copy-password")
@click.option(
    "--repo",
    "-r",
    default=".",
    show_default=True,
    help="Project directory (or its deployment directory)",
)
@click.option(
    "--from",
    "source",
    required=True,
    type=click.Choice(["keychain", "1password"]),
    help="Backend that holds the vault password now",
)
@click.option(
    "--to",
    "target",
    type=click.Choice(["keychain", "1password"]),
    help="Backend to copy it to (default: the one `startup config` chose)",
)
def copy_vault_password(repo, source, target):
    """Copy a project's vault password between password backends.

    The password must open the project's vault before it is written; the
    source entry stays. The password is never printed.

    \b
      startup config set vault-backend 1password --op-vault Startups
      startup secrets copy-password -r . --from keychain
    """
    from cli.ansible_commands import _resolve_project_name
    from cli.vault_backends import get_backend
    from cli.vault_handoff import copy_vault_password as copy_password

    source_backend = get_backend(source)
    target_backend = get_backend(target)
    if source_backend.name == target_backend.name:
        raise click.UsageError(
            f"--from and --to are both {source}. Pass --to, or choose another "
            "backend with startup config set vault-backend."
        )
    working_dir = Path(_password_scope(repo))
    entry = copy_password(
        _resolve_project_name(working_dir), working_dir, source_backend, target_backend
    )
    click.echo(
        f"Copied {entry} from {source_backend.label} to {target_backend.label}. "
        f"The entry in {source_backend.label} is unchanged."
    )


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
    raise click.exceptions.Exit(1)


@secrets.command("get-file")
@click.option(
    "--file",
    "-f",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def get_vault_file(file):
    """Write a vaulted file to stdout for piping, using the password backend or the CI environment."""
    from cli.ansible_commands import resolve_vault_password
    from cli.vault.process import VaultError, decrypt

    encrypted = file.read_bytes()
    if not encrypted.startswith(b"$ANSIBLE_VAULT;"):
        raise click.ClickException("The requested file is not an Ansible Vault file.")
    password = resolve_vault_password(None, _password_scope(str(file)))
    try:
        value = decrypt(encrypted, password)
    except VaultError:
        raise click.ClickException(
            "Could not decrypt the requested Vault file."
        ) from None
    sys.stdout.buffer.write(value)


@secrets.command("get-field")
@click.option(
    "--file", "-f", required=True, help="Path to the file containing the vault field"
)
@click.option("--field", required=True, help="Name of the vault field to retrieve")
@click.option(
    "--vault-password",
    "-p",
    default=None,
    help=(
        "Vault password. Omit it to read the password from the configured "
        "backend (`startup config`, default: macOS Keychain), the way `startup ansible` does."
    ),
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def get_vault_field(file, field, vault_password, verbose):
    """Get the decrypted value of a vault field from a file"""
    from cli.ansible_commands import resolve_vault_password
    from cli.vault.fields import get_inline_vault_value

    file_path = Path(file)
    if not file_path.exists():
        click.echo(f"Error: File {file} does not exist", err=True)
        raise click.exceptions.Exit(1)

    vault_password = resolve_vault_password(vault_password, _password_scope(file))

    if verbose:
        click.echo(f"Getting value for field {field} from file {file}", err=True)

    value = get_inline_vault_value(file_path, field, vault_password, verbose)
    if value is None:
        click.echo(f"Error: Could not retrieve value for field {field}", err=True)
        raise click.exceptions.Exit(1)

    click.echo(value)
    return 0


@secrets.command("update-inline-field")
@click.option(
    "--file", "-f", required=True, help="Path to the file containing the vault field"
)
@click.option("--field", required=True, help="Name of the vault field to update")
@click.option("--value", required=True, help="New value for the field")
@click.option(
    "--vault-password",
    "-p",
    default=None,
    help=(
        "Vault password. Omit it to read the password from the configured "
        "backend (`startup config`, default: macOS Keychain), the way `startup ansible` does."
    ),
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
def update_inline_vault_field_cmd(file, field, value, vault_password, verbose):
    """Update the value of an inline vault field in a file"""
    from cli.ansible_commands import resolve_vault_password
    from cli.vault.fields import update_inline_vault_field

    file_path = Path(file)
    if not file_path.exists():
        click.echo(f"Error: File {file} does not exist", err=True)
        raise click.exceptions.Exit(1)

    vault_password = resolve_vault_password(vault_password, _password_scope(file))

    if verbose:
        click.echo(f"Updating value for field {field} in file {file}")

    success = update_inline_vault_field(file_path, field, value, vault_password)
    if not success:
        click.echo(f"Error: Could not update value for field {field}", err=True)
        raise click.exceptions.Exit(1)

    click.echo(f"Successfully updated value for {field}")
    return 0
