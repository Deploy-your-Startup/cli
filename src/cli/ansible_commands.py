"""Shared Ansible helper commands for project deployments."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click
import yaml


DEFAULT_SHARED_DIR = ".shared-roles"
DEFAULT_VERSION = "main"
SPARSE_PATHS = [
    "roles",
    "ansible.cfg",
    "requirements.yml",
    "backup-playbook.yml",
    "restore-playbook.yml",
    "update-vms-playbook.yml",
    "inventory.ini",
    "inventory.hcloud.yml",
]

ROOT_SHARED_FILES = [
    "ansible.cfg",
    "requirements.yml",
    "backup-playbook.yml",
    "restore-playbook.yml",
    "update-vms-playbook.yml",
    "inventory.ini",
    "inventory.hcloud.yml",
]
DEFAULT_SHARED_REPO_NAME = "deploy-your-startup"


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    command_env.pop("VIRTUAL_ENV", None)
    if env:
        command_env.update(env)

    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            env=command_env,
            input=input_text,
            text=True,
            check=True,
            capture_output=capture_output,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        rendered_command = " ".join(command)
        if details:
            raise click.ClickException(
                f"Command failed: {rendered_command}\n{details}"
            ) from exc
        raise click.ClickException(f"Command failed: {rendered_command}") from exc


def _resolve_working_dir(working_directory: str) -> Path:
    return Path(working_directory).resolve()


def keychain_service_name(project_name: str) -> str:
    return f"VAULT_PASSWORD_{project_name}".upper().replace("-", "_")


def _keychain_vault_password(project_name: str) -> str:
    service_name = keychain_service_name(project_name)

    try:
        result = _run_command(
            [
                "security",
                "find-generic-password",
                "-a",
                os.environ.get("USER", ""),
                "-s",
                service_name,
                "-w",
            ],
            cwd=Path.cwd(),
            capture_output=True,
        )
    except click.ClickException as exc:
        raise click.ClickException(
            f'No vault password found in macOS Keychain for "{service_name}". '
            f"Store it first, for example from the portfolio hub with: scripts/secrets.sh store {project_name}"
        ) from exc

    vault_password = result.stdout.strip()
    if not vault_password:
        raise click.ClickException(
            f'Found empty macOS Keychain entry for "{service_name}".'
        )
    return vault_password


def resolve_vault_password(
    vault_password: str | None,
    vault_password_from_keychain: bool,
    working_directory: str,
) -> str:
    project_name = _resolve_project_name(_resolve_working_dir(working_directory))
    service_name = keychain_service_name(project_name)

    if vault_password and vault_password_from_keychain:
        raise click.ClickException(
            "Use either --vault-password or --vault-password-from-keychain, not both."
        )
    if vault_password:
        return vault_password

    if vault_password_from_keychain:
        return _keychain_vault_password(project_name)

    if os.environ.get("STARTUP_DISABLE_KEYCHAIN_VAULT", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        raise click.ClickException(
            "No vault password provided. Set --vault-password, or remove STARTUP_DISABLE_KEYCHAIN_VAULT to allow the default macOS Keychain lookup."
        )

    try:
        return _keychain_vault_password(project_name)
    except click.ClickException as exc:
        raise click.ClickException(
            f'No vault password provided and no macOS Keychain entry found for "{service_name}".\n'
            "\nDefault behavior:\n"
            "- deployment commands use the macOS Keychain by default\n"
            "- explicit --vault-password overrides the Keychain lookup\n"
            "\nTo fix:\n"
            f"- create the Keychain entry from the portfolio hub: scripts/secrets.sh store {project_name}\n"
            "- or rerun with --vault-password <password>"
        ) from exc


def _ansible_env(
    working_dir: Path, shared_dir: str = DEFAULT_SHARED_DIR
) -> dict[str, str]:
    """Build an environment dict with ANSIBLE_CONFIG pointing to the shared config."""
    env = os.environ.copy()
    ansible_cfg = working_dir / shared_dir / "ansible.cfg"
    if ansible_cfg.exists():
        env["ANSIBLE_CONFIG"] = str(ansible_cfg)
    return env


def _extract_github_owner(remote_url: str) -> str | None:
    ssh_match = re.match(
        r"git@github\.com:(?P<owner>[^/]+)/[^/]+(?:\.git)?$", remote_url
    )
    if ssh_match:
        return ssh_match.group("owner")

    https_match = re.match(
        r"https://github\.com/(?P<owner>[^/]+)/[^/]+(?:\.git)?$", remote_url
    )
    if https_match:
        return https_match.group("owner")

    return None


def _normalize_repo_url(repo_url: str) -> str:
    normalized = repo_url.strip()
    if normalized.startswith("git@github.com:"):
        normalized = normalized.replace("git@github.com:", "https://github.com/", 1)
    if normalized.startswith("ssh://git@github.com/"):
        normalized = normalized.replace(
            "ssh://git@github.com/", "https://github.com/", 1
        )
    normalized = re.sub(
        r"https://x-access-token:[^@]+@github\.com/", "https://github.com/", normalized
    )
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.rstrip("/")


def _infer_roles_owner(working_dir: Path) -> str | None:
    explicit_owner = os.getenv("STARTUP_ANSIBLE_REPO_OWNER")
    if explicit_owner:
        return explicit_owner

    github_repository_owner = os.getenv("GITHUB_REPOSITORY_OWNER")
    if github_repository_owner:
        return github_repository_owner

    try:
        remote_url = _run_command(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=working_dir,
            capture_output=True,
        ).stdout.strip()
    except click.ClickException:
        return None

    if not remote_url:
        return None

    return _extract_github_owner(remote_url)


def _candidate_repo_urls(working_dir: Path, repo_url: str | None = None) -> list[str]:
    candidates: list[str] = []
    prefer_https = bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))

    if repo_url:
        candidates.append(repo_url)

    env_repo = os.getenv("STARTUP_ANSIBLE_REPO_URL")
    if env_repo:
        candidates.append(env_repo)

    inferred_owner = _infer_roles_owner(working_dir)
    github_token = os.getenv("GITHUB_TOKEN")
    if inferred_owner and github_token:
        candidates.append(
            f"https://x-access-token:{github_token}@github.com/{inferred_owner}/{DEFAULT_SHARED_REPO_NAME}.git"
        )

    if inferred_owner:
        if prefer_https:
            candidates.append(
                f"https://github.com/{inferred_owner}/{DEFAULT_SHARED_REPO_NAME}.git"
            )
            candidates.append(
                f"git@github.com:{inferred_owner}/{DEFAULT_SHARED_REPO_NAME}.git"
            )
        else:
            candidates.append(
                f"git@github.com:{inferred_owner}/{DEFAULT_SHARED_REPO_NAME}.git"
            )
            candidates.append(
                f"https://github.com/{inferred_owner}/{DEFAULT_SHARED_REPO_NAME}.git"
            )

    fallback_owner = "Deploy-your-Startup"
    if github_token:
        candidates.append(
            f"https://x-access-token:{github_token}@github.com/{fallback_owner}/deploy-template.git"
        )
    if prefer_https:
        candidates.extend(
            [
                f"https://github.com/{fallback_owner}/deploy-template.git",
                f"git@github.com:{fallback_owner}/deploy-template.git",
            ]
        )
    else:
        candidates.extend(
            [
                f"git@github.com:{fallback_owner}/deploy-template.git",
                f"https://github.com/{fallback_owner}/deploy-template.git",
            ]
        )

    unique_candidates: list[str] = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def _copy_local_repo(source_dir: Path, target_dir: Path) -> Path:
    if not (source_dir / "roles").exists():
        raise click.ClickException(
            f"Local shared roles source '{source_dir}' does not contain a roles directory."
        )

    if target_dir.exists():
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir / "roles", target_dir / "roles")

    ansible_cfg = source_dir / "ansible.cfg"
    if ansible_cfg.exists():
        shutil.copy2(ansible_cfg, target_dir / "ansible.cfg")

    requirements_file = source_dir / "requirements.yml"
    if requirements_file.exists():
        shutil.copy2(requirements_file, target_dir / "requirements.yml")

    backup_playbook = source_dir / "backup-playbook.yml"
    if backup_playbook.exists():
        shutil.copy2(backup_playbook, target_dir / "backup-playbook.yml")

    restore_playbook = source_dir / "restore-playbook.yml"
    if restore_playbook.exists():
        shutil.copy2(restore_playbook, target_dir / "restore-playbook.yml")

    update_vms_playbook = source_dir / "update-vms-playbook.yml"
    if update_vms_playbook.exists():
        shutil.copy2(update_vms_playbook, target_dir / "update-vms-playbook.yml")

    inventory_ini = source_dir / "inventory.ini"
    if inventory_ini.exists():
        shutil.copy2(inventory_ini, target_dir / "inventory.ini")

    inventory_hcloud = source_dir / "inventory.hcloud.yml"
    if inventory_hcloud.exists():
        shutil.copy2(inventory_hcloud, target_dir / "inventory.hcloud.yml")

    return target_dir


def _configure_sparse_checkout(target_dir: Path, cwd: Path) -> None:
    _run_command(
        ["git", "-C", str(target_dir), "config", "core.sparseCheckout", "true"],
        cwd=cwd,
    )
    _run_command(
        ["git", "-C", str(target_dir), "sparse-checkout", "init", "--no-cone"],
        cwd=cwd,
    )
    _run_command(
        [
            "git",
            "-C",
            str(target_dir),
            "sparse-checkout",
            "set",
            "roles/*",
            "/ansible.cfg",
            "/backup-playbook.yml",
            "/restore-playbook.yml",
            "/requirements.yml",
            "/inventory.ini",
            "/inventory.hcloud.yml",
        ],
        cwd=cwd,
    )
    # Only apply sparse checkout to working tree if HEAD exists (skip on fresh init)
    try:
        _run_command(
            ["git", "-C", str(target_dir), "rev-parse", "--verify", "HEAD"],
            cwd=cwd,
            capture_output=True,
        )
    except click.ClickException:
        return
    _run_command(
        ["git", "-C", str(target_dir), "read-tree", "-mu", "HEAD"],
        cwd=cwd,
    )
    _run_command(
        [
            "git",
            "-C",
            str(target_dir),
            "checkout",
            "--force",
            "HEAD",
            "--",
            *ROOT_SHARED_FILES,
        ],
        cwd=cwd,
    )


def _normalize_inventory_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        unsafe_value = value.get("__ansible_unsafe")
        if isinstance(unsafe_value, str):
            return unsafe_value
    return str(value)


def _is_up_to_date(target_dir: Path, working_dir: Path, version: str) -> bool:
    """Check if the local checkout is already up to date with the remote."""
    try:
        local_head = _run_command(
            ["git", "-C", str(target_dir), "rev-parse", "HEAD"],
            cwd=working_dir,
            capture_output=True,
        ).stdout.strip()
        remote_ref = _run_command(
            ["git", "-C", str(target_dir), "ls-remote", "origin", version],
            cwd=working_dir,
            capture_output=True,
        ).stdout.strip()
        if remote_ref:
            remote_sha = remote_ref.split()[0]
            return local_head == remote_sha
    except (click.ClickException, IndexError):
        pass
    return False


def clone_or_update_shared_roles(
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> Path:
    working_dir = _resolve_working_dir(working_directory)
    target_dir = working_dir / shared_dir
    candidates = _candidate_repo_urls(working_dir, repo_url)
    last_error: Exception | None = None

    if (
        target_dir.exists()
        and not (target_dir / ".git").exists()
        and (target_dir / "roles").exists()
        and not refresh
    ):
        click.echo(f"Using existing shared roles directory '{target_dir}' ...")
        return target_dir

    if target_dir.exists() and (target_dir / ".git").exists():
        try:
            current_remote = _run_command(
                ["git", "-C", str(target_dir), "remote", "get-url", "origin"],
                cwd=working_dir,
                capture_output=True,
            ).stdout.strip()
            normalized_current_remote = _normalize_repo_url(current_remote)
            normalized_candidates = {
                _normalize_repo_url(candidate) for candidate in candidates
            }

            if normalized_current_remote not in normalized_candidates:
                click.echo(
                    f"Replacing shared roles checkout from '{current_remote}' with current configured source ..."
                )
                shutil.rmtree(target_dir)
                raise FileNotFoundError(
                    "Recreate shared roles checkout with new source"
                )

            # Quick check: skip fetch/pull if already up to date
            if _is_up_to_date(target_dir, working_dir, version):
                click.echo("Shared roles are up to date.")
                return target_dir

            _configure_sparse_checkout(target_dir, working_dir)
            _run_command(
                ["git", "-C", str(target_dir), "fetch", "--tags", "origin", version],
                cwd=working_dir,
            )
            _run_command(
                ["git", "-C", str(target_dir), "checkout", version], cwd=working_dir
            )
            _configure_sparse_checkout(target_dir, working_dir)
            if re.fullmatch(r"[A-Za-z0-9._/-]+", version):
                try:
                    _run_command(
                        [
                            "git",
                            "-C",
                            str(target_dir),
                            "pull",
                            "--ff-only",
                            "origin",
                            version,
                        ],
                        cwd=working_dir,
                    )
                except subprocess.CalledProcessError:
                    pass
            return target_dir
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            last_error = exc

    if target_dir.exists() and not (target_dir / ".git").exists():
        click.echo(
            f"Refreshing shared roles directory '{target_dir}' (non-git copy mode) ..."
        )
        shutil.rmtree(target_dir)

    for candidate in candidates:
        try:
            local_candidate = Path(candidate).expanduser()
            if local_candidate.exists() and local_candidate.is_dir():
                click.echo(
                    f"Copying shared Ansible roles from local directory {local_candidate} ..."
                )
                return _copy_local_repo(local_candidate.resolve(), target_dir)

            if target_dir.exists():
                shutil.rmtree(target_dir)
            click.echo(f"Cloning shared Ansible roles from {candidate} ...")
            _run_command(["git", "init", str(target_dir)], cwd=working_dir)
            _run_command(
                ["git", "-C", str(target_dir), "remote", "add", "origin", candidate],
                cwd=working_dir,
            )
            _configure_sparse_checkout(target_dir, working_dir)
            _run_command(
                [
                    "git",
                    "-C",
                    str(target_dir),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    version,
                ],
                cwd=working_dir,
            )
            _run_command(
                ["git", "-C", str(target_dir), "checkout", "FETCH_HEAD"],
                cwd=working_dir,
            )
            return target_dir
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            last_error = exc

    raise click.ClickException(
        "Could not clone shared Ansible roles repository. "
        "Set STARTUP_ANSIBLE_REPO_URL if you need a custom clone URL."
    ) from last_error


def _find_uv() -> str:
    """Find the uv binary, checking common locations if not on PATH."""
    uv_path = shutil.which("uv")
    if uv_path:
        return uv_path
    for candidate in (
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
    ):
        if candidate.exists():
            return str(candidate)
    return "uv"


def install_collections(
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
) -> None:
    working_dir = _resolve_working_dir(working_directory)
    files_to_install = [
        working_dir / shared_dir / "requirements.yml",
        working_dir / "requirements.yml",
    ]

    for requirements_file in files_to_install:
        if requirements_file.exists():
            click.echo(f"Installing Ansible collections from {requirements_file} ...")
            _run_command(
                [
                    _find_uv(),
                    "run",
                    "--project",
                    str(working_dir),
                    "ansible-galaxy",
                    "collection",
                    "install",
                    "-r",
                    str(requirements_file),
                ],
                cwd=working_dir,
            )


def setup_ansible(
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> Path:
    shared_roles_dir = clone_or_update_shared_roles(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )
    install_collections(working_directory=working_directory, shared_dir=shared_dir)
    return shared_roles_dir


def setup(
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> Path:
    working_dir = _resolve_working_dir(working_directory)
    click.echo("Installing Python dependencies...")
    _run_command([_find_uv(), "sync"], cwd=working_dir)
    return setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )


def get_hcloud_token(
    working_directory: str,
    vault_password: str,
    environment: str,
    shared_dir: str = DEFAULT_SHARED_DIR,
) -> str:
    working_dir = _resolve_working_dir(working_directory)
    env = _ansible_env(working_dir, shared_dir)
    result = _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-vault",
            "view",
            f"hcloud_token_{environment}",
            "--vault-password-file",
            "/bin/cat",
        ],
        cwd=working_dir,
        env=env,
        input_text=vault_password,
        capture_output=True,
    )
    token = result.stdout.strip()
    if not token:
        raise click.ClickException(
            f"Could not read hcloud_token for environment '{environment}' from Ansible Vault."
        )
    return token


def _validated_environment(environment: str) -> None:
    if environment not in {"production", "staging"}:
        raise click.ClickException("--environment must be production or staging")


def _resolve_project_name(working_dir: Path) -> str:
    return (
        working_dir.parent.name
        if working_dir.name == "deployment"
        else working_dir.name
    )


def _resolve_playbook_path(
    working_dir: Path, playbook: str, label: str, shared_dir: str = DEFAULT_SHARED_DIR
) -> Path:
    playbook_path = working_dir / playbook
    if not playbook_path.exists():
        playbook_path = working_dir / shared_dir / playbook
    if not playbook_path.exists():
        raise click.ClickException(f"{label} playbook not found: {playbook_path}")
    return playbook_path


def _latest_matching_file(search_root: Path, pattern: str) -> Path | None:
    candidates = [path for path in search_root.rglob(pattern) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _resolve_restore_file(
    explicit_file: str | None,
    *,
    search_root: Path,
    pattern: str,
    label: str,
) -> Path:
    if explicit_file:
        resolved = Path(explicit_file).expanduser().resolve()
        if not resolved.exists():
            raise click.ClickException(f"{label} backup file not found: {resolved}")
        if not resolved.is_file():
            raise click.ClickException(f"{label} backup path is not a file: {resolved}")
        return resolved

    resolved = _latest_matching_file(search_root, pattern)
    if resolved is None:
        raise click.ClickException(
            f"Could not find a {label.lower()} backup file matching '{pattern}' in {search_root}"
        )
    return resolved


def run_deploy(
    vault_password: str,
    environment: str,
    service: str = "all",
    *,
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    working_dir = _resolve_working_dir(working_directory)
    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )
    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token
    _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-playbook",
            "playbook.yml",
            "--vault-password-file",
            "/bin/cat",
            "--tags",
            service or "all",
            "--skip-tags",
            "infrastructure",
        ],
        cwd=working_dir,
        env=env,
        input_text=vault_password,
    )


def run_infrastructure(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    _validated_environment(environment)
    working_dir = _resolve_working_dir(working_directory)
    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )
    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token
    _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-playbook",
            "playbook.yml",
            "--vault-password-file",
            "/bin/cat",
            "--tags",
            "infrastructure",
            "-l",
            f"{environment},provision-infrastructure",
        ],
        cwd=working_dir,
        env=env,
        input_text=vault_password,
    )


def _extract_master_host(inventory_data: dict) -> str | None:
    for group_name in ("k3s_masters", "masters", "control_plane"):
        hosts = inventory_data.get(group_name, {}).get("hosts", [])
        if hosts:
            return hosts[0]

    for group_name, group_data in inventory_data.items():
        if group_name == "_meta":
            continue
        for host_name in group_data.get("hosts", []) or []:
            if "master" in host_name:
                return host_name
    return None


def _derive_context_name(remote_name: str, environment: str, env_suffix: bool) -> str:
    match = re.match(r"^([^-]+-[^-]+).*$", remote_name.lower())
    project_name = re.sub(r"[^a-z0-9._-]+", "-", match.group(1) if match else "")
    context = project_name or f"k3s-{environment}"
    if env_suffix and environment:
        context = f"{context}-{environment}"
    return context


def run_kubeconfig(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    inventory: str = "inventory.hcloud.yml",
    out: str | None = None,
    ssh_user: str = "root",
    master_host: str | None = None,
    context_name: str | None = None,
    env_suffix: bool = True,
    make_current: bool = True,
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> Path:
    working_dir = _resolve_working_dir(working_directory)
    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )
    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token

    inventory_path = working_dir / inventory
    if not inventory_path.exists():
        inventory_path = working_dir / shared_dir / inventory
    inventory_result = _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-inventory",
            "-i",
            str(inventory_path),
            "--list",
        ],
        cwd=working_dir,
        env=env,
        capture_output=True,
    )
    inventory_data = json.loads(inventory_result.stdout)
    resolved_master_host = master_host or _extract_master_host(inventory_data)
    if not resolved_master_host:
        raise click.ClickException(
            "Could not auto-detect k3s master host from inventory. Specify --master-host."
        )

    hostvars = (
        inventory_data.get("_meta", {})
        .get("hostvars", {})
        .get(resolved_master_host, {})
    )
    master_ip = (
        _normalize_inventory_value(hostvars.get("ansible_host"))
        or _normalize_inventory_value(hostvars.get("public_ipv4"))
        or _normalize_inventory_value(hostvars.get("public_ip"))
    )
    if not master_ip:
        raise click.ClickException(
            f"Could not resolve IP for host '{resolved_master_host}'."
        )

    output_path = (
        Path(out).expanduser()
        if out
        else Path.home() / ".kube" / f"k3s-{environment}.yaml"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        _run_command(
            [
                "scp",
                "-o",
                "StrictHostKeyChecking=accept-new",
                f"{ssh_user}@{master_ip}:/etc/rancher/k3s/k3s.yaml",
                str(tmp_path),
            ],
            cwd=working_dir,
        )

        kubeconfig = yaml.safe_load(tmp_path.read_text(encoding="utf-8"))
        for cluster in kubeconfig.get("clusters", []):
            if cluster.get("cluster", {}).get("server"):
                cluster["cluster"]["server"] = f"https://{master_ip}:6443"

        if context_name:
            context = context_name
        else:
            remote_name = (
                _run_command(
                    [
                        "ssh",
                        "-o",
                        "StrictHostKeyChecking=accept-new",
                        f"{ssh_user}@{master_ip}",
                        "hostname",
                    ],
                    cwd=working_dir,
                    capture_output=True,
                )
                .stdout.splitlines()[0]
                .strip()
            )
            context = _derive_context_name(remote_name, environment, env_suffix)

        for item in kubeconfig.get("clusters", []):
            if item.get("name") == "default":
                item["name"] = context
        for item in kubeconfig.get("users", []):
            if item.get("name") == "default":
                item["name"] = context
        for item in kubeconfig.get("contexts", []):
            if item.get("name") == "default":
                item["name"] = context
            if item.get("context", {}).get("cluster") == "default":
                item["context"]["cluster"] = context
            if item.get("context", {}).get("user") == "default":
                item["context"]["user"] = context
        if kubeconfig.get("current-context") == "default":
            kubeconfig["current-context"] = context

        output_path.write_text(
            yaml.safe_dump(kubeconfig, sort_keys=False), encoding="utf-8"
        )

        if not shutil.which("kubectl"):
            raise click.ClickException(
                "kubectl not found in PATH. Please install kubectl."
            )

        kube_dir = Path.home() / ".kube"
        kube_dir.mkdir(parents=True, exist_ok=True)
        main_config = kube_dir / "config"

        if main_config.exists():
            for delete_target in ("context", "cluster", "user"):
                subprocess.run(
                    ["kubectl", "config", f"delete-{delete_target}", context],
                    check=False,
                    capture_output=True,
                    text=True,
                )

        merged = _run_command(
            ["kubectl", "config", "view", "--flatten"],
            cwd=working_dir,
            env={**os.environ, "KUBECONFIG": f"{main_config}:{output_path}"},
            capture_output=True,
        )
        main_config.write_text(merged.stdout, encoding="utf-8")

        if make_current:
            _run_command(["kubectl", "config", "use-context", context], cwd=working_dir)

        click.echo(f"Wrote kubeconfig to: {output_path}")
        click.echo(f"Imported context into ~/.kube/config as: {context}")
        return output_path
    finally:
        tmp_path.unlink(missing_ok=True)


def run_backup(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    backup_dir: str | None = None,
    playbook: str = "backup-playbook.yml",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    _validated_environment(environment)
    working_dir = _resolve_working_dir(working_directory)
    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )

    playbook_path = _resolve_playbook_path(working_dir, playbook, "Backup", shared_dir)
    project_name = _resolve_project_name(working_dir)
    resolved_backup_dir = (
        Path(backup_dir).expanduser()
        if backup_dir
        else Path.home() / "Backups" / project_name
    )
    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token

    _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-playbook",
            str(playbook_path),
            "--vault-password-file",
            "/bin/cat",
            "--extra-vars",
            f"project_name={project_name} backup_environment={environment} local_backup_root={resolved_backup_dir}",
        ],
        cwd=working_dir,
        env=env,
        input_text=vault_password,
    )


def run_update_vms(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    playbook: str = "update-vms-playbook.yml",
    limit: str | None = None,
    reboot: bool = False,
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    _validated_environment(environment)
    working_dir = _resolve_working_dir(working_directory)
    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )

    playbook_path = _resolve_playbook_path(
        working_dir, playbook, "Update VMs", shared_dir
    )
    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token

    effective_limit = f"{environment},{limit}" if limit else environment
    extra_vars = {
        "update_reboot": reboot,
        "update_environment": environment,
    }

    _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-playbook",
            str(playbook_path),
            "--vault-password-file",
            "/bin/cat",
            "-l",
            effective_limit,
            "--extra-vars",
            json.dumps(extra_vars),
        ],
        cwd=working_dir,
        env=env,
        input_text=vault_password,
    )


def run_restore(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    backup_dir: str | None = None,
    db_file: str | None = None,
    media_file: str | None = None,
    playbook: str = "restore-playbook.yml",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
    restore_db: bool = True,
    restore_media: bool = True,
    confirm: bool = False,
) -> None:
    _validated_environment(environment)
    if not restore_db and not restore_media:
        raise click.ClickException("Enable at least one restore target.")
    if not confirm:
        raise click.ClickException(
            "Restore is destructive. Re-run with --yes after verifying the backup files."
        )

    working_dir = _resolve_working_dir(working_directory)
    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )

    playbook_path = _resolve_playbook_path(working_dir, playbook, "Restore", shared_dir)
    project_name = _resolve_project_name(working_dir)
    search_root = (
        Path(backup_dir).expanduser().resolve()
        if backup_dir
        else (Path.home() / "Backups" / project_name).resolve()
    )
    if not search_root.exists():
        raise click.ClickException(f"Backup directory not found: {search_root}")

    resolved_db_file = None
    if restore_db:
        resolved_db_file = _resolve_restore_file(
            db_file,
            search_root=search_root,
            pattern=f"{project_name}-db-*.sql*",
            label="Database",
        )

    resolved_media_file = None
    if restore_media:
        resolved_media_file = _resolve_restore_file(
            media_file,
            search_root=search_root,
            pattern=f"{project_name}-media-*.tar*",
            label="Media",
        )

    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token

    extra_vars = {
        "project_name": project_name,
        "restore_environment": environment,
        "restore_db": restore_db,
        "restore_media": restore_media,
        "db_backup_file": str(resolved_db_file) if resolved_db_file else "",
        "media_backup_file": str(resolved_media_file) if resolved_media_file else "",
    }

    _run_command(
        [
            _find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-playbook",
            str(playbook_path),
            "--vault-password-file",
            "/bin/cat",
            "--extra-vars",
            json.dumps(extra_vars),
        ],
        cwd=working_dir,
        env=env,
        input_text=vault_password,
    )
