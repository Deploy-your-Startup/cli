import contextlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .vault.common import generate_random_secret
from .vault.fields import (
    normalize_vault_block,
    regen_vault_string,
    replace_block,
    verify_inline_field,
)
from .vault.process import VaultError, decrypt, encrypt, rekey


def is_full_vault_file(path: Path) -> bool:
    """Check if a file is a full vault file (not just containing inline vault blocks)."""
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        return first_line.startswith("$ANSIBLE_VAULT")
    except (OSError, UnicodeDecodeError, IndexError):
        # Unreadable, binary, or empty — not a vault file either way.
        return False


def rotate_full_vault_file(
    path: Path,
    vault_password: str,
    work_dir: Path | None = None,
    new_content: str | None = None,
    dry_run: bool = False,
    dry_dir: Path | None = None,
    new_password: str | None = None,
    verify_password: bool = True,
):
    """Rotate a full vault file or replace its content."""
    try:
        if new_content is not None:
            encrypted = encrypt(new_content, new_password or vault_password)
        elif verify_password:
            encrypted = rekey(
                path.read_bytes(), vault_password, new_password or vault_password
            )
        else:
            encrypted = encrypt(
                generate_random_secret(), new_password or vault_password
            )
        if dry_run:
            if dry_dir is not None:
                rel = (
                    path.relative_to(work_dir)
                    if work_dir and path.is_relative_to(work_dir)
                    else path.name
                )
                out = dry_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(encrypted)
            return True
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        path.write_bytes(encrypted)
        return True
    except (OSError, ValueError):
        print(f"Could not update Vault file {path}.", file=sys.stderr)
        return False


# Directories that never hold a project's own secrets but do hold enormous
# numbers of YAML files. A `deployment/.venv` with ansible installed carries
# over 3000 of them, which made `-r .` appear to hang.
SCAN_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".shared-roles",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dry-run-output",
    }
)


def find_yaml_files(root: Path):
    """Every .yml/.yaml under root, skipping vendored and generated trees."""
    return [
        path
        for pattern in ("*.yml", "*.yaml")
        for path in root.rglob(pattern)
        if not SCAN_EXCLUDED_DIRS.intersection(path.relative_to(root).parts[:-1])
    ]


def load_text(path: Path):
    try:
        return path.read_bytes().decode()
    except (OSError, UnicodeDecodeError) as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return None


def backup_and_write(path: Path, content: str):
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
        print(f"Backup saved: {bak}")
    path.write_bytes(content.encode())


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

    except yaml.YAMLError:
        print("Could not parse decrypted YAML.", file=sys.stderr)
        return None


NO_OPERATION_HELP = """
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

YAML_EXTENSIONS = {".yml", ".yaml"}


class _Abort(Exception):
    """Stop the update; ``update_secrets`` returns failure with what was written so far."""

    def __init__(self, password_verification_failed: bool, discard_updates=False):
        super().__init__()
        self.password_verification_failed = password_verification_failed
        # Report no updated files even if earlier phases wrote some.
        self.discard_updates = discard_updates


@dataclass
class _SecretsRun:
    """State shared by the phases of one ``update_secrets`` call."""

    work_dir: Path
    is_file: bool
    vault_password: str
    dry_run: bool
    dry_dir: Path
    verbose: bool
    verify_password: bool
    updated: list = field(default_factory=list)
    written_fields: set = field(default_factory=set)
    password_verification_failed: bool = False

    def rel(self, path: Path):
        """How a scanned YAML file is reported: relative to the scan, or its name."""
        return path.name if self.is_file else path.relative_to(self.work_dir)

    def write(self, path: Path, rel, text: str) -> None:
        """Write ``text`` to ``path``, or to its dry-run copy."""
        if self.dry_run:
            out = self.dry_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(text.encode())
            if self.verbose:
                print(f"Dry-run: wrote {out}")
        else:
            backup_and_write(path, text)
            if self.verbose:
                print(f"Updated {path}")

    def can_decrypt(self, path: Path, rel) -> bool:
        try:
            decrypt(path.read_bytes(), self.vault_password)
        # Ansible reports a wrong vault password as any of several exception
        # types (AnsibleError, ValueError, binascii.Error, UnicodeDecodeError, ...),
        # so this stays broad on purpose.
        except Exception:  # noqa: BLE001
            print(f"Error: Cannot decrypt vault file {rel} with provided password.")
            self.password_verification_failed = True
            return False
        return True

    def rotate(
        self, path: Path, rel, *, new_content: str | None, done: str, failed: str
    ) -> None:
        """Re-encrypt a full vault file, optionally with new content."""
        success = rotate_full_vault_file(
            path,
            self.vault_password,
            work_dir=self.work_dir,
            new_content=new_content,
            dry_run=self.dry_run,
            dry_dir=self.dry_dir,
            verify_password=self.verify_password,
        )
        if success:
            self.updated.append(rel)
            if self.verbose:
                print(f"{done}: {rel}")
            return
        print(f"{failed} {rel}")
        if not self.verify_password:
            print(
                "Consider using --verify-password to check if the password is correct"
            )


def _rotate_vault_files(run: _SecretsRun, vault_files) -> None:
    print(f"Processing vault files: {vault_files}")
    for vault_file_path in vault_files:
        matches = list(run.work_dir.glob(f"**/{vault_file_path}"))
        if not matches:
            print(f"Warning: Vault file {vault_file_path} not found in {run.work_dir}")
            continue
        for path in matches:
            rel = path.relative_to(run.work_dir)
            if not is_full_vault_file(path):
                print(f"Warning: {rel} is not a vault file, skipping")
                continue
            if run.verify_password and not run.can_decrypt(path, rel):
                continue
            run.rotate(
                path,
                rel,
                new_content=None,
                done="Rotated vault file",
                failed="Error rotating vault file",
            )


def _collect_updates(updates, vault_fields, set_field, secret_length, verbose) -> dict:
    """Merge every way of naming a field into one ``{field: plaintext}`` mapping."""
    updates_dict = {}
    if isinstance(updates, str):
        # A path to a JSON file of field values.
        try:
            updates_dict.update(json.loads(Path(updates).read_text()))
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Error loading updates JSON: {e}", file=sys.stderr)
            raise _Abort(False, discard_updates=True) from e
    elif isinstance(updates, dict):
        updates_dict.update(updates)
    if vault_fields:
        updates_dict.update(
            {f: generate_random_secret(secret_length) for f in vault_fields}
        )
    for field_name, value in set_field or []:
        updates_dict[field_name] = value
        if verbose:
            print(f"Setting {field_name} to explicitly provided value")
    return updates_dict


def _update_inline_blocks(
    run: _SecretsRun, yaml_files, updates_dict: dict, only_existing: bool
) -> None:
    """Replace ``field: !vault |`` blocks in plain YAML files."""
    for yml in yaml_files:
        rel = run.rel(yml)
        if is_full_vault_file(yml):
            if run.verbose:
                print(f"Skip {rel}, it's a full vault file (not inline blocks)")
            continue
        text = load_text(yml)
        if text is None:
            continue
        modified = False
        for var, plain in updates_dict.items():
            has_block = re.search(
                rf"^[ \t]*{re.escape(var)}:\s*!vault \|", text, re.MULTILINE
            )
            if not has_block and only_existing:
                if run.verbose:
                    print(f"Skip {var} in {rel}, no existing block.")
                continue
            if (
                has_block
                and run.verify_password
                and not verify_inline_field(text, var, run.vault_password)
            ):
                print(
                    f"Error: Cannot decrypt existing vault for {var} in {rel}. Incorrect password."
                )
                raise _Abort(password_verification_failed=True)

            new_block = regen_vault_string(var, plain, run.vault_password)
            new_text, count = replace_block(text, var, new_block)
            if count:
                modified = True
                text = new_text
                run.written_fields.add(var)
                if run.verbose:
                    print(f"Replaced {var} ({count}) in {rel}")
        if modified:
            run.updated.append(rel)
            run.write(yml, rel, text)


def _encrypted_yaml_files(run: _SecretsRun, repo_path: Path, yaml_files) -> list:
    if (
        run.is_file
        and is_full_vault_file(repo_path)
        and repo_path.suffix.lower() in YAML_EXTENSIONS
    ):
        files = [repo_path]
    else:
        files = [
            yml
            for yml in yaml_files
            if is_full_vault_file(yml) and yml.suffix.lower() in YAML_EXTENSIONS
        ]
    if files:
        print("Processing field updates in fully encrypted YAML files")
    return files


def _update_encrypted_yaml_files(run: _SecretsRun, files, updates_dict: dict) -> None:
    """Set top-level fields inside fully encrypted YAML files."""
    for yml in files:
        rel = run.rel(yml)
        try:
            decrypted_content = decrypt(yml.read_bytes(), run.vault_password).decode(
                "utf-8"
            )
            new_content = update_fields_in_yaml(
                decrypted_content, updates_dict, run.vault_password
            )
            if new_content is None:
                if run.verbose:
                    print(f"No fields to update in: {rel}")
                continue
            if run.verbose:
                print(f"Updating fields in encrypted YAML file: {rel}")
            success = rotate_full_vault_file(
                yml,
                run.vault_password,
                work_dir=run.work_dir,
                new_content=new_content,
                dry_run=run.dry_run,
                dry_dir=run.dry_dir,
                verify_password=False,  # Already verified by decrypting
            )
            if success:
                run.updated.append(rel)
                run.written_fields.update(
                    updates_dict.keys() & yaml.safe_load(new_content).keys()
                )
            else:
                print(f"Error updating encrypted YAML file: {rel}")
        # Top-level boundary: any failure here is reported to the user and
        # handled, never surfaced as a traceback.
        except Exception as e:
            print(f"Error processing encrypted YAML file {rel}: {e}", file=sys.stderr)
            raise _Abort(password_verification_failed=True) from e


def _create_missing_fields(
    run: _SecretsRun, missing: list, updates_dict: dict, create_in
) -> None:
    """Append vault blocks for fields that exist nowhere yet to ``create_in``."""
    target = Path(create_in)
    if not target.is_absolute():
        target = run.work_dir / create_in
    if not target.exists():
        print(f"Error: --create-in file does not exist: {target}")
        raise _Abort(run.password_verification_failed)
    if is_full_vault_file(target):
        print("Cannot append inline fields to a fully encrypted file.", file=sys.stderr)
        raise _Abort(run.password_verification_failed)

    rel = (
        target.relative_to(run.work_dir)
        if run.work_dir in target.parents
        else target.name
    )
    # A dry run that already changed this file builds on its preview copy.
    preview = run.dry_dir / rel
    source = preview if run.dry_run and preview.exists() else target
    text = load_text(source) or ""
    if text and not text.endswith("\n"):
        text += "\n"
    for var in missing:
        block = regen_vault_string(var, updates_dict[var], run.vault_password)
        # Same indentation the replace path produces, so a created field is
        # indistinguishable from one that was already there.
        text += "\n" + normalize_vault_block(block)
        run.written_fields.add(var)

    if run.dry_run:
        out = run.dry_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode())
        print(f"Dry-run: would create {', '.join(missing)} in {rel}")
    else:
        backup_and_write(target, text)
        print(f"Created {', '.join(missing)} in {rel}")
    if rel not in run.updated:
        run.updated.append(rel)


def _update_fields(
    run: _SecretsRun,
    repo_path: Path,
    yaml_files,
    updates_dict: dict,
    *,
    only_existing: bool,
    create_in,
) -> None:
    _update_inline_blocks(run, yaml_files, updates_dict, only_existing)
    _update_encrypted_yaml_files(
        run, _encrypted_yaml_files(run, repo_path, yaml_files), updates_dict
    )
    # Fields that matched no vault block anywhere. Either create them in the
    # file the caller named, or say so - never pretend they were stored.
    missing = [v for v in updates_dict if v not in run.written_fields]
    if not missing or only_existing:
        return
    if create_in:
        _create_missing_fields(run, missing, updates_dict, create_in)
        return
    print(
        "Error: no vault block exists for: "
        + ", ".join(sorted(missing))
        + "\nNothing was written for these. Pass --create-in <file.yml> "
        "to add them, or --only-existing to skip them on purpose."
    )
    raise _Abort(run.password_verification_failed)


def _replace_file_contents(run: _SecretsRun, set_file_content) -> None:
    print("Processing file content replacements")
    for file_path, new_content in set_file_content:
        matches = list(run.work_dir.glob(f"**/{file_path}"))
        if not matches:
            matches = [run.work_dir / file_path]
            if run.verbose:
                print(
                    f"File {file_path} not found in {run.work_dir}; creating it before encrypting"
                )
        for path in matches:
            path.parent.mkdir(parents=True, exist_ok=True)
            rel = path.relative_to(run.work_dir)
            if (
                run.verify_password
                and is_full_vault_file(path)
                and not run.can_decrypt(path, rel)
            ):
                continue
            run.rotate(
                path,
                rel,
                new_content=new_content,
                done="Updated content of file",
                failed="Error updating content of file",
            )


def _updated_field_names(updates, vault_fields, set_field) -> list:
    field_names = []
    if updates and isinstance(updates, dict):
        field_names.extend(updates.keys())
    elif updates and isinstance(updates, str):
        # Not a readable JSON object — field names stay unset.
        with contextlib.suppress(
            OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError
        ):
            field_names.extend(json.loads(Path(updates).read_text()).keys())
    if vault_fields:
        field_names.extend(vault_fields)
    if set_field:
        field_names.extend([name for name, _ in set_field])
    return field_names


def _print_summary(
    run: _SecretsRun, updates, vault_fields, vault_files, set_field, set_file_content
) -> tuple:
    dry_run_prefix = "DRY RUN: " if run.dry_run else ""
    if run.password_verification_failed:
        print(
            "⚠️ Some updates were skipped due to vault password verification failures."
        )
        if run.dry_run:
            print(
                "Note: In dry-run mode, only files that could be successfully processed were written to dry-run-output/"
            )
        return False, run.updated, True
    if not run.updated:
        print(f"{dry_run_prefix}No updates applied.")
        return True, [], False

    field_operation = updates or vault_fields or set_field
    if set_file_content and not (field_operation or vault_files):
        print(f"{dry_run_prefix}Updated file content:")
    elif vault_files and not field_operation:
        print(f"{dry_run_prefix}Rotated vault files:")
    elif field_operation:
        field_names = _updated_field_names(updates, vault_fields, set_field)
        print(f"{dry_run_prefix}Updated fields {', '.join(field_names)} in:")
    prefix = "dry-run-output/" if run.dry_run else ""
    for f in run.updated:
        print(f"  - {prefix}{f}")
    return True, run.updated, False


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
    create_in=None,
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
    - create_in: YAML file to append fields to that have no vault block anywhere.
      Without it such a field is reported as an error; previously it was a
      silent no-op, so a secret could appear to have been stored when nothing
      had been written.

    Returns:
    - tuple of (success, updated_files, password_verification_failed)
    """
    repo_path = Path(repo)
    is_file = repo_path.exists() and repo_path.is_file()
    if not repo_path.exists():
        work_dir = Path(tempfile.mkdtemp(prefix="repo_scan_"))
        print(f"Cloning {repo} into {work_dir}...")
        subprocess.run(["git", "clone", repo, str(work_dir)], check=True)
    else:
        work_dir = repo_path.parent.resolve() if is_file else repo_path.resolve()

    dry_dir = Path("dry-run-output")
    if dry_run:
        if dry_dir.exists():
            shutil.rmtree(dry_dir)
        dry_dir.mkdir()

    run = _SecretsRun(
        work_dir=work_dir,
        is_file=is_file,
        vault_password=vault_password,
        dry_run=dry_run,
        dry_dir=dry_dir,
        verbose=verbose,
        verify_password=verify_password,
    )
    try:
        if not (
            updates or vault_fields or vault_files or set_field or set_file_content
        ):
            print(NO_OPERATION_HELP.strip())
            return False, [], False

        if is_file:
            if verbose:
                print(f"Processing a single YAML file: {repo_path}")
            yaml_files = [repo_path]
        else:
            yaml_files = find_yaml_files(work_dir)

        if vault_files:
            _rotate_vault_files(run, vault_files)
        if updates or vault_fields or set_field:
            updates_dict = _collect_updates(
                updates, vault_fields, set_field, secret_length, verbose
            )
            _update_fields(
                run,
                repo_path,
                yaml_files,
                updates_dict,
                only_existing=only_existing,
                create_in=create_in,
            )
        if set_file_content:
            _replace_file_contents(run, set_file_content)
        return _print_summary(
            run, updates, vault_fields, vault_files, set_field, set_file_content
        )
    except _Abort as abort:
        updated = [] if abort.discard_updates else run.updated
        return False, updated, abort.password_verification_failed
    except VaultError as error:
        print(str(error), file=sys.stderr)
        return False, run.updated, run.password_verification_failed
    finally:
        if not repo_path.exists():
            shutil.rmtree(work_dir)
