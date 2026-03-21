import json
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
import secrets
import string
from ansible.parsing.vault import VaultLib, VaultSecret
from ansible.constants import DEFAULT_VAULT_IDENTITY


def verify_vault_password(vault_text, vault_password):
    """Verify if the provided vault password can decrypt the vault text."""
    if not vault_text.startswith("$ANSIBLE_VAULT"):
        return False

    try:
        vault_secret = VaultSecret(vault_password.encode())
        vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
        # Try to decrypt - if it fails, the password is incorrect
        vault.decrypt(vault_text.encode())
        return True
    except Exception:
        return False


def is_full_vault_file(path: Path) -> bool:
    """Check if a file is a full vault file (not just containing inline vault blocks)."""
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        return first_line.startswith("$ANSIBLE_VAULT")
    except Exception:
        return False


def rotate_full_vault_file(
    path: Path,
    vault_password: str,
    work_dir: Path = None,
    new_content: str = None,
    dry_run: bool = False,
    dry_dir: Path = None,
    new_password: str = None,
    verify_password: bool = True,
):
    """Rotate a full vault file or replace its content."""
    vault_secret = VaultSecret(vault_password.encode())
    new_vault_secret = VaultSecret((new_password or vault_password).encode())

    if new_content is not None:
        # We're replacing the content
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tf:
            tf.write(new_content)

        # Encrypt the new content
        vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
        encrypted = vault.encrypt(new_content.encode())

        if dry_run and dry_dir:
            # In dry run mode, create the output file in the dry run directory
            if work_dir and path.is_relative_to(work_dir):
                rel_path = path.relative_to(work_dir)
            else:
                rel_path = path.name
            out = dry_dir / rel_path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(encrypted)
            return True
        else:
            # Backup and write
            if path.exists():
                bak = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, bak)
                print(f"Backup saved: {bak}")
            path.write_bytes(encrypted)
            return True
    else:
        # We're just rotating the vault password (re-encrypting with the same password)
        encrypted = None

        if verify_password:
            try:
                # Try to decrypt to verify the password
                vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
                content = vault.decrypt(path.read_bytes())

                # Re-encrypt with the new password if provided, otherwise use the same password
                # (effectively just rotating the salt if same password)
                new_vault = VaultLib([(DEFAULT_VAULT_IDENTITY, new_vault_secret)])
                encrypted = new_vault.encrypt(content)
            except Exception as e:
                print(f"Error rotating vault file {path}: {e}", file=sys.stderr)
                return False
        else:
            # When verification is not required, use ansible-vault command-line tool
            # to create a new encrypted file with the new password
            try:
                # Create a temporary file for the vault password
                with tempfile.NamedTemporaryFile(
                    delete=False, mode="w"
                ) as new_pass_file:
                    new_pass_file.write(new_password or vault_password)
                    new_pass_path = new_pass_file.name

                # Create a temporary file for the output
                with tempfile.NamedTemporaryFile(delete=False) as output_file:
                    output_path = output_file.name

                # Use ansible-vault to create a new encrypted file
                # First, create a plaintext file with random content
                random_content = "".join(
                    secrets.choice(string.ascii_letters + string.digits)
                    for _ in range(32)
                )
                with tempfile.NamedTemporaryFile(delete=False, mode="w") as plain_file:
                    plain_file.write(random_content)
                    plain_path = plain_file.name

                # Encrypt the plaintext file with the new password
                subprocess.run(
                    [
                        "ansible-vault",
                        "encrypt",
                        "--vault-password-file",
                        new_pass_path,
                        "--output",
                        output_path,
                        plain_path,
                    ],
                    check=True,
                )

                # Read the encrypted content
                encrypted = Path(output_path).read_bytes()

                # Clean up temporary files
                Path(new_pass_path).unlink()
                Path(plain_path).unlink()
                Path(output_path).unlink()
            except Exception as e:
                print(f"Error creating new vault file {path}: {e}", file=sys.stderr)
                return False

        # Write the encrypted content to the output file
        if encrypted:
            if dry_run and dry_dir:
                # In dry run mode, create the output file in the dry run directory
                if work_dir and path.is_relative_to(work_dir):
                    rel_path = path.relative_to(work_dir)
                else:
                    rel_path = path.name
                out = dry_dir / rel_path
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(encrypted)
                return True
            else:
                # Backup and write
                if path.exists():
                    bak = path.with_suffix(path.suffix + ".bak")
                    shutil.copy2(path, bak)
                    print(f"Backup saved: {bak}")
                path.write_bytes(encrypted)
                return True

        return False


def regen_vault_string(name, plaintext, vault_pass_file):
    """Encrypt a plaintext into an inline Ansible vault block."""
    # Use '--' to prevent plaintext starting with '-' being parsed as option
    cmd = [
        "ansible-vault",
        "encrypt_string",
        "--name",
        name,
        "--vault-password-file",
        vault_pass_file,
        "--",
        plaintext,
    ]
    try:
        proc = subprocess.run(
            cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return proc.stdout.decode()
    except subprocess.CalledProcessError as e:
        print(f"Error encrypting {name}: {e.stderr.decode()}", file=sys.stderr)
        raise


def extract_vault_block(content, var_name):
    """Extract the vault block for a variable name."""
    pattern = (
        rf"(?m)^(?P<indent>[ \t]*){re.escape(var_name)}:"  # indent + var_name:
        r"\s*!vault \|(?:\r?\n[ \t].*)*"  # vault block
    )
    match = re.search(pattern, content)
    if match:
        # Extract the vault content (without the variable name and !vault | marker)
        full_match = match.group(0)
        lines = full_match.split("\n")
        # Skip the first line which contains the variable name
        vault_lines = []
        for line in lines[1:]:  # Start from the second line
            if line.strip() and not line.strip().startswith(var_name):
                # Remove indentation
                indent = len(line) - len(line.lstrip())
                vault_lines.append(line[indent:])
        return "\n".join(vault_lines)
    return None


def replace_block(content, var_name, new_block, verbose=False):
    """Replace all contiguous vault blocks for var_name, ensuring consistent indentation."""

    def _repl(match):
        indent = match.group("indent")
        # Split the new_block into lines
        lines = new_block.rstrip("\n").split("\n")

        # Extract just the variable name and vault marker from the first line
        # The format is typically "var_name: !vault |"
        first_line_parts = lines[0].split(":", 1)
        if len(first_line_parts) == 2:
            var_part = first_line_parts[0]
            vault_marker = first_line_parts[1].strip()
            # Reconstruct the first line with proper indentation
            result = [f"{indent}{var_part}: {vault_marker}"]

            # For all vault content lines, use consistent indentation (2 spaces)
            for line in lines[1:]:
                result.append(indent + "  " + line.strip())

            return "\n".join(result) + "\n"
        else:
            # Fallback if parsing fails
            return (
                indent
                + lines[0]
                + "\n"
                + "\n".join(indent + "  " + line.strip() for line in lines[1:])
                + "\n"
            )

    # Match var_name line and all following indented lines, capturing leading indent
    pattern = (
        rf"(?m)^(?P<indent>[ \t]*){re.escape(var_name)}:"  # indent + var_name:
        r"\s*!vault \|(?:\r?\n[ \t].*)*"  # vault block
    )

    # Debug output for verbose logging only
    debug_match = re.search(pattern, content)
    if not debug_match and verbose:
        print(f"Warning: No vault block found for {var_name} with pattern {pattern}")

    new_content, count = re.subn(pattern, _repl, content)
    if count == 0 and verbose:
        print(f"Warning: Failed to replace vault block for {var_name}")
    return new_content, count


def generate_random_secret(length=32):
    """Generate a URL-safe random secret not starting with '-' or '_'"""
    alphabet = string.ascii_letters + string.digits
    first = secrets.choice(string.ascii_letters)
    rest = "".join(secrets.choice(alphabet + "-_") for _ in range(length - 1))
    return first + rest


def find_yaml_files(root: Path):
    return list(root.rglob("*.yml")) + list(root.rglob("*.yaml"))


def load_text(path: Path):
    try:
        return path.read_text()
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return None


def backup_and_write(path: Path, content: str):
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_text(path.read_text())
        print(f"Backup saved: {bak}")
    path.write_text(content)


def update_fields_in_yaml(yaml_content, updates, vault_file):
    """Update fields in a YAML content (already decrypted) and return the updated content."""
    import yaml

    # Parse the YAML content
    try:
        data = yaml.safe_load(yaml_content) or {}

        # Apply the updates
        modified = False
        for field, value in updates.items():
            if field in data:
                data[field] = value
                modified = True

        if not modified:
            return None

        # Convert back to YAML string
        return yaml.dump(data, default_flow_style=False)

    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}", file=sys.stderr)
        return None


def update_secrets(
    repo,
    vault_password,
    updates=None,
    vault_fields=None,
    vault_files=None,
    secret_length=32,
    dry_run=False,
    verbose=False,
    only_existing=False,
    verify_password=False,
    set_field=None,
    set_file_content=None,
):
    """
    Core function to update vault secrets in a repository.

    Parameters:
    - repo: Directory or Git URL of repo to scan, or a direct path to a YAML file
    - vault_password: Vault password for encryption
    - updates: Dictionary mapping var names to plaintext secrets or path to JSON file
    - vault_fields: List of var names to auto-generate new random secrets
    - vault_files: List of full vault files to rotate (re-encrypt with same password)
    - secret_length: Length for generated secrets
    - dry_run: If True, write changed files to dry-run-output/ instead of modifying originals
    - verbose: If True, enable verbose logging
    - only_existing: If True, only update vars with existing vault blocks
    - verify_password: If True, verify vault password can decrypt existing secrets before updating
    - set_field: List of (field, value) tuples to set specific field values
    - set_file_content: List of (file_path, content) tuples to set specific file contents

    Returns:
    - tuple of (success, updated_files, password_verification_failed)
    """
    # Prepare workspace
    repo_path = Path(repo)
    is_file = repo_path.exists() and repo_path.is_file()

    if not repo_path.exists():
        work_dir = Path(tempfile.mkdtemp(prefix="repo_scan_"))
        print(f"Cloning {repo} into {work_dir}...")
        subprocess.run(["git", "clone", repo, str(work_dir)], check=True)
    else:
        work_dir = repo_path.parent.resolve() if is_file else repo_path.resolve()

    # Dry-run output dir
    dry_dir = Path("dry-run-output")
    if dry_run:
        if dry_dir.exists():
            shutil.rmtree(dry_dir)
        dry_dir.mkdir()

    # Temp vault-password file
    with tempfile.NamedTemporaryFile(delete=False, mode="w") as tf:
        tf.write(vault_password)
        vault_file = tf.name

    updated = []
    password_verification_failed = False

    # Ensure at least one action is specified
    if (
        not updates
        and not vault_fields
        and not vault_files
        and not set_field
        and not set_file_content
    ):
        error_msg = """
Error: No update operation specified.

Please specify at least one operation:

INLINE FIELD OPERATIONS (for YAML files with vault blocks):
  --field-random <name>        Generate random value for field
  --field-set <name> <value>   Set specific value for field

FULL FILE OPERATIONS (for encrypted files):
  --file-rotate <path>         Re-encrypt file with same password
  --file-content <path> <content>  Replace file content

Examples:
  # Update a single field with random value
  startup secrets update --repo . --vault-password PASSWORD --field-random backend_db_password
  
  # Set specific field value
  startup secrets update --repo . --vault-password PASSWORD --field-set api_key "my-secret-key"
  
  # Rotate an encrypted file
  startup secrets update --repo . --vault-password PASSWORD --file-rotate secrets.yml

For more help: startup secrets update --help
"""
        print(error_msg.strip())
        return False, [], False

    # If a direct file path was provided, only process that file
    yaml_files_to_process = []
    if is_file:
        if verbose:
            print(f"Processing a single YAML file: {repo_path}")
        yaml_files_to_process = [repo_path]
    else:
        # Find all YAML files in the workspace
        yaml_files_to_process = find_yaml_files(work_dir)

    # Process full vault files if specified
    if vault_files:
        print(f"Processing vault files: {vault_files}")
        for vault_file_path in vault_files:
            # Find the file in the workspace
            matches = list(work_dir.glob(f"**/{vault_file_path}"))
            if not matches:
                print(f"Warning: Vault file {vault_file_path} not found in {work_dir}")
                continue

            for path in matches:
                rel = path.relative_to(work_dir)

                # Verify it's a vault file
                if not is_full_vault_file(path):
                    print(f"Warning: {rel} is not a vault file, skipping")
                    continue

                # If verify-password is enabled, check if we can decrypt the vault file
                if verify_password:
                    try:
                        # Try to decrypt to verify the password
                        vault_secret = VaultSecret(vault_password.encode())
                        vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
                        vault.decrypt(path.read_bytes())
                    except Exception:
                        print(
                            f"Error: Cannot decrypt vault file {rel} with provided password."
                        )
                        password_verification_failed = True
                        continue

                # Use the rotate_full_vault_file function for both dry run and normal mode
                success = rotate_full_vault_file(
                    path,
                    vault_password,
                    work_dir=work_dir,
                    dry_run=dry_run,
                    dry_dir=dry_dir,
                    verify_password=verify_password,
                )

                if success:
                    updated.append(rel)
                    if verbose:
                        print(f"Rotated vault file: {rel}")
                else:
                    print(f"Error rotating vault file {rel}")
                    if not verify_password:
                        print(
                            "Consider using --verify-password to check if the password is correct"
                        )

    # Process inline vault blocks
    if updates or vault_fields or set_field:
        # Build updates mapping
        updates_dict = {}
        if updates:
            # If updates is a string, try to load it as a JSON file
            if isinstance(updates, str):
                try:
                    updates_dict.update(json.loads(Path(updates).read_text()))
                except (json.JSONDecodeError, FileNotFoundError) as e:
                    print(f"Error loading updates JSON: {e}", file=sys.stderr)
                    return False, [], False
            elif isinstance(updates, dict):
                updates_dict.update(updates)

        if vault_fields:
            updates_dict.update(
                {f: generate_random_secret(secret_length) for f in vault_fields}
            )

        if set_field:
            # Add specific field/value pairs
            for field, value in set_field:
                updates_dict[field] = value
                if verbose:
                    print(f"Setting {field} to explicitly provided value")

        for yml in yaml_files_to_process:
            # Skip full vault files when processing inline blocks
            if is_full_vault_file(yml):
                rel = yml.relative_to(work_dir) if not is_file else yml.name
                if verbose:
                    print(f"Skip {rel}, it's a full vault file (not inline blocks)")
                continue

            text = load_text(yml)
            if text is None:
                continue
            modified = False
            rel = yml.relative_to(work_dir) if not is_file else yml.name

            for var, plain in updates_dict.items():
                # Check if the variable exists with a vault block
                if not re.search(rf"^[ \t]*{re.escape(var)}:\s*!vault \|", text, re.M):
                    if only_existing:
                        if verbose:
                            print(f"Skip {var} in {rel}, no existing block.")
                        continue
                else:
                    # If verify-password is enabled, check if we can decrypt the existing vault
                    if verify_password:
                        vault_block = extract_vault_block(text, var)
                        if vault_block and not verify_vault_password(
                            vault_block, vault_password
                        ):
                            print(
                                f"Error: Cannot decrypt existing vault for {var} in {rel}. Incorrect password."
                            )
                            password_verification_failed = True
                            continue

                new_block = regen_vault_string(var, plain, vault_file)
                new_text, count = replace_block(text, var, new_block, verbose)
                if count:
                    modified = True
                    text = new_text
                    if verbose:
                        print(f"Replaced {var} ({count}) in {rel}")
            if modified:
                updated.append(rel)
                if dry_run:
                    # Create the output file in the dry run directory with the same structure
                    out = dry_dir / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(text)
                    if verbose:
                        print(f"Dry-run: wrote {out}")
                else:
                    backup_and_write(yml, text)
                    if verbose:
                        print(f"Updated {yml}")

    # Process fully encrypted YAML files for field updates
    if updates or vault_fields or set_field:
        # Process only YAML files
        yaml_extensions = {".yml", ".yaml"}
        # If a direct file path is provided and it's a fully encrypted YAML file, process only that file
        if (
            is_file
            and is_full_vault_file(repo_path)
            and repo_path.suffix.lower() in yaml_extensions
        ):
            yaml_files_for_encryption = [repo_path]
            print("Processing field updates in fully encrypted YAML files")
        else:
            # Otherwise process all fully encrypted YAML files found in the workspace
            yaml_files_for_encryption = [
                yml
                for yml in yaml_files_to_process
                if is_full_vault_file(yml) and yml.suffix.lower() in yaml_extensions
            ]
            if yaml_files_for_encryption:
                print("Processing field updates in fully encrypted YAML files")

        for yml in yaml_files_for_encryption:
            rel = yml.relative_to(work_dir) if not is_file else yml.name

            try:
                # Decrypt the file
                vault_secret = VaultSecret(vault_password.encode())
                vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
                decrypted_content = vault.decrypt(yml.read_bytes()).decode("utf-8")

                # Update the YAML content
                new_content = update_fields_in_yaml(
                    decrypted_content, updates_dict, vault_file
                )
                if new_content is not None:
                    # Re-encrypt the updated content
                    if verbose:
                        print(f"Updating fields in encrypted YAML file: {rel}")

                    success = rotate_full_vault_file(
                        yml,
                        vault_password,
                        work_dir=work_dir,
                        new_content=new_content,
                        dry_run=dry_run,
                        dry_dir=dry_dir,
                        verify_password=False,  # Already verified by decrypting
                    )

                    if success:
                        updated.append(rel)
                    else:
                        print(f"Error updating encrypted YAML file: {rel}")
                else:
                    if verbose:
                        print(f"No fields to update in: {rel}")

            except Exception as e:
                print(
                    f"Error processing encrypted YAML file {rel}: {e}", file=sys.stderr
                )
                if verify_password:
                    password_verification_failed = True

    # Process file content replacements if specified
    if set_file_content:
        print("Processing file content replacements")
        for file_path, new_content in set_file_content:
            # Find the file in the workspace
            matches = list(work_dir.glob(f"**/{file_path}"))
            if not matches:
                print(f"Warning: File {file_path} not found in {work_dir}")
                continue

            for path in matches:
                rel = path.relative_to(work_dir)

                # If verify-password is enabled and it's a vault file, check if we can decrypt it
                if verify_password and is_full_vault_file(path):
                    try:
                        vault_secret = VaultSecret(vault_password.encode())
                        vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
                        vault.decrypt(path.read_bytes())
                    except Exception:
                        print(
                            f"Error: Cannot decrypt vault file {rel} with provided password."
                        )
                        password_verification_failed = True
                        continue

                # Use the rotate_full_vault_file function with new content
                success = rotate_full_vault_file(
                    path,
                    vault_password,
                    work_dir=work_dir,
                    new_content=new_content,  # Pass the new content here
                    dry_run=dry_run,
                    dry_dir=dry_dir,
                    verify_password=verify_password,
                )

                if success:
                    updated.append(rel)
                    if verbose:
                        print(f"Updated content of file: {rel}")
                else:
                    print(f"Error updating content of file {rel}")
                    if not verify_password:
                        print(
                            "Consider using --verify-password to check if the password is correct"
                        )

    # Cleanup
    Path(vault_file).unlink()
    if not Path(repo).exists():
        shutil.rmtree(work_dir)

    # Summary
    if password_verification_failed:
        print(
            "⚠️ Some updates were skipped due to vault password verification failures."
        )
        if dry_run:
            print(
                "Note: In dry-run mode, only files that could be successfully processed were written to dry-run-output/"
            )
        return False, updated, True
    elif updated:
        prefix = "dry-run-output/" if dry_run else ""

        # Add a clear dry-run indicator to the output
        dry_run_prefix = "DRY RUN: " if dry_run else ""

        # Different summary messages based on what was updated
        if set_file_content and not (
            updates or vault_fields or set_field or vault_files
        ):
            print(f"{dry_run_prefix}Updated file content:")
        elif vault_files and not (updates or vault_fields or set_field):
            print(f"{dry_run_prefix}Rotated vault files:")
        elif updates or vault_fields or set_field:
            field_names = []
            if updates and isinstance(updates, dict):
                field_names.extend(updates.keys())
            elif updates and isinstance(updates, str):
                try:
                    field_names.extend(json.loads(Path(updates).read_text()).keys())
                except Exception:
                    pass
            if vault_fields:
                field_names.extend(vault_fields)
            if set_field:
                field_names.extend([field for field, _ in set_field])

            print(f"{dry_run_prefix}Updated fields {', '.join(field_names)} in:")

        for f in updated:
            print(f"  - {prefix}{f}")
        return True, updated, False
    else:
        # Also add dry-run indicator here
        dry_run_prefix = "DRY RUN: " if dry_run else ""
        print(f"{dry_run_prefix}No updates applied.")
        return True, [], False
