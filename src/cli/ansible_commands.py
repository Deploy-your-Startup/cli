"""Shared Ansible helper commands for project deployments."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click
import yaml

from . import kubeconfig_files, playbook_inputs, tailnet
from .ansible_bin import ansible_bin
from .kubeconfig_files import (
    configure_context as _configure_kubeconfig_context,  # used by tests
)
from .playbook_inputs import BYOS_INVENTORY
from .vault_backends import (
    PasswordNotFound,
    get_backend,
    keychain_service_name,  # re-exported for cli.wizard.*
)
from .vault_handoff import recover_from_password_manager

__all__ = ["keychain_service_name"]


DEFAULT_SHARED_DIR = ".shared-roles"
DEFAULT_VERSION = "main"
ROOT_SHARED_FILES = [
    "ansible.cfg",
    "requirements.yml",
    "backup-playbook.yml",
    "restore-playbook.yml",
    "update-vms-playbook.yml",
    "os-upgrade-playbook.yml",
    "k3s-upgrade-playbook.yml",
    "dns-playbook.yml",
    "inventory.ini",
    "inventory.hcloud.yml",
]

SPARSE_PATHS = ["roles", *ROOT_SHARED_FILES]
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


def resolve_vault_password(
    vault_password: str | None,
    working_directory: str,
    backend: str | None = None,
) -> str:
    """Resolve the vault password.

    An explicit ``--vault-password`` always wins. Otherwise the password is
    read from the configured backend (macOS Keychain unless `startup config`
    chose 1Password), keyed by the project name derived from
    ``working_directory``. When the backend has no entry, an interactive run
    asks for the password manager copy and stores it.
    """
    if vault_password:
        return vault_password
    if environment_password := os.environ.get("STARTUP_VAULT_PASSWORD"):
        return environment_password

    project_name = _resolve_project_name(_resolve_working_dir(working_directory))

    if os.environ.get("STARTUP_DISABLE_KEYCHAIN_VAULT", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        raise click.ClickException(
            "No vault password provided. Pass --vault-password, or unset "
            "STARTUP_DISABLE_KEYCHAIN_VAULT to allow the configured password backend."
        )

    password_backend = get_backend(backend)
    try:
        return password_backend.read(project_name)
    except PasswordNotFound:
        # A computer without the entry: ask for the password manager copy once
        # instead of failing every command.
        recovered = recover_from_password_manager(
            project_name, _resolve_working_dir(working_directory), password_backend
        )
        if recovered is None:
            raise
        return recovered


def _ansible_env(
    working_dir: Path, shared_dir: str = DEFAULT_SHARED_DIR
) -> dict[str, str]:
    """Build an environment dict with ANSIBLE_CONFIG pointing to the shared config."""
    env = os.environ.copy()
    ansible_cfg = working_dir / shared_dir / "ansible.cfg"
    if ansible_cfg.exists():
        env["ANSIBLE_CONFIG"] = str(ansible_cfg)
    return env


# `/bin/cat` echoes the password piped to stdin (`input_text`), so the vault
# password reaches Ansible without ever appearing in a command line.
VAULT_PASSWORD_FROM_STDIN = ["--vault-password-file", "/bin/cat"]


def _uv_run(working_dir: Path, *args: str) -> list[str]:
    """A command that runs inside the project's uv environment, where Ansible lives."""
    return [_find_uv(), "run", "--project", str(working_dir), *args]


def _playbook_command(working_dir: Path, playbook: str, *args: str) -> list[str]:
    return _uv_run(working_dir, ansible_bin("ansible-playbook"), playbook, *args)


def _playbook_options(
    *,
    tags: list[str] | None = None,
    skip_tags: list[str] | None = None,
    limit: list[str] | None = None,
    extra_vars: str | None = None,
) -> list[str]:
    options: list[str] = []
    if tags:
        options += ["--tags", ",".join(tags)]
    if skip_tags:
        options += ["--skip-tags", ",".join(skip_tags)]
    if limit:
        options += ["-l", ",".join(limit)]
    if extra_vars:
        options += ["--extra-vars", extra_vars]
    return options


def _git(
    repo_dir: Path, *args: str, cwd: Path, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    return _run_command(
        ["git", "-C", str(repo_dir), *args], cwd=cwd, capture_output=capture_output
    )


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
    normalized = normalized.removesuffix(".git")
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


def _github_clone_urls(
    repository: str, github_token: str | None, prefer_https: bool
) -> list[str]:
    """Clone URLs for ``owner/name``: token first, then SSH and HTTPS.

    CI has no SSH key but may have HTTPS access, so there HTTPS goes first.
    """
    https = f"https://github.com/{repository}.git"
    ssh = f"git@github.com:{repository}.git"
    with_token = (
        [f"https://x-access-token:{github_token}@github.com/{repository}.git"]
        if github_token
        else []
    )
    return with_token + ([https, ssh] if prefer_https else [ssh, https])


def _candidate_repo_urls(working_dir: Path, repo_url: str | None = None) -> list[str]:
    """Sources for the shared roles, most specific first, without duplicates.

    An explicit URL wins, then STARTUP_ANSIBLE_REPO_URL, then the owner's copy
    of the roles, and finally the public deploy-template.
    """
    prefer_https = bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))
    github_token = os.getenv("GITHUB_TOKEN")
    candidates = [
        url for url in (repo_url, os.getenv("STARTUP_ANSIBLE_REPO_URL")) if url
    ]

    inferred_owner = _infer_roles_owner(working_dir)
    if inferred_owner:
        candidates += _github_clone_urls(
            f"{inferred_owner}/{DEFAULT_SHARED_REPO_NAME}", github_token, prefer_https
        )
    candidates += _github_clone_urls(
        "Deploy-your-Startup/deploy-template", github_token, prefer_https
    )
    return list(dict.fromkeys(candidates))


def _copy_local_repo(source_dir: Path, target_dir: Path) -> Path:
    if not (source_dir / "roles").exists():
        raise click.ClickException(
            f"Local shared roles source '{source_dir}' does not contain a roles directory."
        )

    if target_dir.exists():
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir / "roles", target_dir / "roles")

    for filename in ROOT_SHARED_FILES:
        source_file = source_dir / filename
        if source_file.exists():
            shutil.copy2(source_file, target_dir / filename)

    return target_dir


def _configure_sparse_checkout(target_dir: Path, cwd: Path) -> None:
    sparse_entries = ["roles/*", *[f"/{path}" for path in ROOT_SHARED_FILES]]

    _git(target_dir, "config", "core.sparseCheckout", "true", cwd=cwd)
    _git(target_dir, "sparse-checkout", "init", "--no-cone", cwd=cwd)
    _git(target_dir, "sparse-checkout", "set", *sparse_entries, cwd=cwd)
    # Only apply sparse checkout to working tree if HEAD exists (skip on fresh init)
    try:
        _git(target_dir, "rev-parse", "--verify", "HEAD", cwd=cwd, capture_output=True)
    except click.ClickException:
        return
    _git(target_dir, "read-tree", "-mu", "HEAD", cwd=cwd)
    existing_root_files = [
        path
        for path in ROOT_SHARED_FILES
        if subprocess.run(
            ["git", "-C", str(target_dir), "cat-file", "-e", f"HEAD:{path}"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    ]
    if existing_root_files:
        _git(
            target_dir,
            "checkout",
            "--force",
            "HEAD",
            "--",
            *existing_root_files,
            cwd=cwd,
        )


def _local_changes(target_dir: Path, working_dir: Path) -> str:
    """Return `git status --porcelain` for the shared-roles checkout.

    Empty when the checkout matches its HEAD. Anything else means the roles
    Ansible is about to run are not the roles the pinned commit contains.
    """
    try:
        return _git(
            target_dir, "status", "--porcelain", cwd=working_dir, capture_output=True
        ).stdout.strip()
    except click.ClickException:
        return ""


def _is_up_to_date(target_dir: Path, working_dir: Path, version: str) -> bool:
    """Check if the local checkout is already up to date with the remote."""
    # A dirty tree is not up to date, whatever HEAD says. Skipping the refresh
    # here would print "Shared roles are up to date" and then run Ansible
    # against locally modified roles without a word about it.
    if _local_changes(target_dir, working_dir):
        return False
    try:
        local_head = _git(
            target_dir, "rev-parse", "HEAD", cwd=working_dir, capture_output=True
        ).stdout.strip()
        remote_ref = _git(
            target_dir,
            "ls-remote",
            "origin",
            version,
            cwd=working_dir,
            capture_output=True,
        ).stdout.strip()
        if remote_ref:
            remote_sha = remote_ref.split()[0]
            return local_head == remote_sha
    except (click.ClickException, IndexError):
        pass
    return False


def _requested_roles_version(working_dir: Path, version: str) -> str:
    """Apply the project's pins: shared-roles.ref, else Copier's deploy_ref."""
    pin_file = working_dir / "shared-roles.ref"
    if pin_file.exists():
        pinned = pin_file.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", pinned):
            raise click.ClickException(
                "shared-roles.ref must contain a full Git commit SHA."
            )
        if version not in {DEFAULT_VERSION, pinned}:
            raise click.ClickException(
                "Requested roles differ from shared-roles.ref. Update the reviewed pin first."
            )
        return pinned
    if version != DEFAULT_VERSION:
        return version
    answers = working_dir.parent / ".copier-answers.yml"
    if not answers.exists():
        return version
    values = yaml.safe_load(answers.read_text())
    deployment_ref = values.get("deploy_ref") if isinstance(values, dict) else None
    if not deployment_ref:
        return version
    if (
        not isinstance(deployment_ref, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", deployment_ref)
        or ".." in deployment_ref
    ):
        raise click.ClickException("Invalid deploy_ref in Copier answers.")
    return deployment_ref


def _warn_about_local_changes(
    target_dir: Path, working_dir: Path, version: str
) -> None:
    # The fetch/checkout that follows keeps uncommitted edits, so say plainly
    # that this run will not use the roles the pinned commit contains.
    # `.shared-roles` is a managed checkout — edits there are almost always a
    # leftover experiment.
    dirty = _local_changes(target_dir, working_dir)
    if not dirty:
        return
    click.echo(
        f"WARNING: '{target_dir}' has uncommitted changes; Ansible "
        f"will run against them, not against {version}:"
    )
    for line in dirty.splitlines():
        click.echo(f"  {line}")
    click.echo(f"  Discard them with: git -C {target_dir} checkout -- .")


def _update_existing_checkout(
    target_dir: Path, working_dir: Path, version: str, candidates: list[str]
) -> Path:
    """Bring an existing git checkout to ``version``.

    Raises FileNotFoundError after deleting a checkout whose remote is no
    longer a configured source, so the caller clones it afresh.
    """
    current_remote = _git(
        target_dir, "remote", "get-url", "origin", cwd=working_dir, capture_output=True
    ).stdout.strip()
    configured = {_normalize_repo_url(candidate) for candidate in candidates}
    if _normalize_repo_url(current_remote) not in configured:
        click.echo(
            f"Replacing shared roles checkout from '{current_remote}' with current configured source ..."
        )
        shutil.rmtree(target_dir)
        raise FileNotFoundError("Recreate shared roles checkout with new source")

    # Re-apply the sparse cone before deciding anything. The set of files
    # SPARSE_PATHS asks for grows with the CLI, and a checkout created by an
    # older version keeps its narrower cone in .git/info/sparse-checkout
    # forever: HEAD matches the remote, so the fast path below returns, and the
    # never-checked-out file stays missing. `git status` does not report it —
    # the file is in the commit, just not in the worktree — so this is invisible
    # until a playbook is not found. Materializing the cone first costs three
    # cheap git calls and makes the fast path safe.
    _configure_sparse_checkout(target_dir, working_dir)

    if _is_up_to_date(target_dir, working_dir, version):
        click.echo("Shared roles are up to date.")
        return target_dir

    _warn_about_local_changes(target_dir, working_dir, version)
    _configure_sparse_checkout(target_dir, working_dir)
    _git(target_dir, "fetch", "--tags", "origin", version, cwd=working_dir)
    _git(target_dir, "checkout", version, cwd=working_dir)
    _configure_sparse_checkout(target_dir, working_dir)
    if re.fullmatch(r"[A-Za-z0-9._/-]+", version):
        with contextlib.suppress(subprocess.CalledProcessError):
            _git(target_dir, "pull", "--ff-only", "origin", version, cwd=working_dir)
    return target_dir


def _fresh_checkout(
    candidate: str, target_dir: Path, working_dir: Path, version: str
) -> Path:
    """Copy a local source directory, or shallow-clone ``version`` of a remote."""
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
    _git(target_dir, "remote", "add", "origin", candidate, cwd=working_dir)
    _configure_sparse_checkout(target_dir, working_dir)
    _git(target_dir, "fetch", "--depth", "1", "origin", version, cwd=working_dir)
    _git(target_dir, "checkout", "FETCH_HEAD", cwd=working_dir)
    return target_dir


_CHECKOUT_ERRORS = (
    click.ClickException,
    subprocess.CalledProcessError,
    FileNotFoundError,
)


def clone_or_update_shared_roles(
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> Path:
    """Materialize the shared roles in ``shared_dir`` and return that directory.

    An existing git checkout is updated in place; anything that fails falls
    back to a fresh copy or clone from each candidate source in turn.
    """
    working_dir = _resolve_working_dir(working_directory)
    version = _requested_roles_version(working_dir, version)
    target_dir = working_dir / shared_dir
    candidates = _candidate_repo_urls(working_dir, repo_url)
    is_git_checkout = (target_dir / ".git").exists()
    last_error: Exception | None = None

    if (
        target_dir.exists()
        and not is_git_checkout
        and (target_dir / "roles").exists()
        and not refresh
    ):
        click.echo(f"Using existing shared roles directory '{target_dir}' ...")
        return target_dir

    if target_dir.exists() and is_git_checkout:
        try:
            return _update_existing_checkout(
                target_dir, working_dir, version, candidates
            )
        except _CHECKOUT_ERRORS as exc:
            last_error = exc

    if target_dir.exists() and not (target_dir / ".git").exists():
        click.echo(
            f"Refreshing shared roles directory '{target_dir}' (non-git copy mode) ..."
        )
        shutil.rmtree(target_dir)

    for candidate in candidates:
        try:
            return _fresh_checkout(candidate, target_dir, working_dir, version)
        except _CHECKOUT_ERRORS as exc:
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
                _uv_run(
                    working_dir,
                    "ansible-galaxy",
                    "collection",
                    "install",
                    "-r",
                    str(requirements_file),
                ),
                cwd=working_dir,
            )


def _shared_roles_fingerprint(root: Path) -> str:
    """Hash role files and the runtime configuration, independent of Git metadata."""
    digest = hashlib.sha256()
    paths = [
        path
        for path in (root / "roles").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    paths += [root / name for name in ROOT_SHARED_FILES if (root / name).is_file()]
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def run_pin(
    working_directory: str = ".",
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
) -> None:
    """Record a resolved owner-repository commit and its runtime contents."""
    working_dir = _resolve_working_dir(working_directory)
    with tempfile.TemporaryDirectory(
        prefix=".startup-pin-", dir=working_dir
    ) as temporary:
        root = clone_or_update_shared_roles(
            working_directory=temporary,
            version=version,
            repo_url=repo_url,
        )
        if not (root / ".git").exists():
            raise click.ClickException(
                "Pinning requires a Git repository, not a local directory export."
            )
        revision = _run_command(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            cwd=working_dir,
            capture_output=True,
        ).stdout.strip()
        fingerprint = _shared_roles_fingerprint(root)
    (working_dir / "shared-roles.sha256").write_text(fingerprint + "\n")
    (working_dir / "shared-roles.ref").write_text(revision + "\n")
    click.echo(f"Pinned shared roles to {revision}. Review and commit both pin files.")


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
    working_dir = _resolve_working_dir(working_directory)
    fingerprint = _shared_roles_fingerprint(shared_roles_dir)
    fingerprint_file = working_dir / "shared-roles.sha256"
    pin_file = working_dir / "shared-roles.ref"
    if pin_file.exists():
        if (
            not fingerprint_file.exists()
            or fingerprint_file.read_text().strip() != fingerprint
        ):
            raise click.ClickException(
                "Shared role contents do not match shared-roles.sha256. "
                "Refusing to run modified or differently exported roles; update the reviewed pin and checksum together."
            )
    elif (shared_roles_dir / ".git").exists():
        revision = _run_command(
            ["git", "-C", str(shared_roles_dir), "rev-parse", "HEAD"],
            cwd=working_dir,
            capture_output=True,
        ).stdout.strip()
        pin_file.write_text(revision + "\n")
        fingerprint_file.write_text(fingerprint + "\n")
        click.echo(
            f"Pinned shared roles to {revision}. Commit shared-roles.ref and shared-roles.sha256."
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
        _uv_run(
            working_dir,
            ansible_bin("ansible-vault"),
            "view",
            f"hcloud_token_{environment}",
            *VAULT_PASSWORD_FROM_STDIN,
        ),
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


def _resolve_k8s_namespace(working_dir: Path) -> str:
    """Read the project's non-secret namespace without loading vaulted YAML."""
    all_vars = working_dir / "group_vars" / "all.yml"
    if not all_vars.exists():
        return "default"

    # Anchored at column 0 on purpose: an indented `k8s_namespace:` is a key
    # inside some other mapping, not the project's namespace.
    namespace_line = re.compile(r"^k8s_namespace:\s*(['\"]?)([^\s#'\"]+)\1\s*(?:#.*)?$")
    for line in all_vars.read_text(encoding="utf-8").splitlines():
        match = namespace_line.match(line)
        if match:
            return match.group(2)
    return "default"


def _is_byos(working_dir: Path) -> bool:
    """A project is bring-your-own-server when it ships a static byos inventory."""
    return (working_dir / BYOS_INVENTORY).exists()


def get_byos_ssh_key(
    working_directory: str,
    vault_password: str,
    shared_dir: str = DEFAULT_SHARED_DIR,
) -> str:
    """Decrypt the deploy SSH private key (ci_ssh_key) from the vault."""
    working_dir = _resolve_working_dir(working_directory)
    env = _ansible_env(working_dir, shared_dir)
    result = _run_command(
        _uv_run(
            working_dir,
            ansible_bin("ansible-vault"),
            "view",
            "ci_ssh_key",
            *VAULT_PASSWORD_FROM_STDIN,
        ),
        cwd=working_dir,
        env=env,
        input_text=vault_password,
        capture_output=True,
    )
    if not result.stdout.strip():
        raise click.ClickException("Could not read ci_ssh_key from Ansible Vault.")
    return result.stdout


@contextlib.contextmanager
def byos_private_key_file(
    working_directory: str,
    vault_password: str,
    shared_dir: str = DEFAULT_SHARED_DIR,
):
    """Decrypt the deploy key into a 0600 temp file; clean it up on exit."""
    ssh_key = get_byos_ssh_key(working_directory, vault_password, shared_dir)
    fd, key_path = tempfile.mkstemp(prefix="byos-deploy-key-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(ssh_key if ssh_key.endswith("\n") else ssh_key + "\n")
        yield key_path
    finally:
        with contextlib.suppress(OSError):
            os.unlink(key_path)


def _run_byos_playbook(
    working_directory: str,
    vault_password: str,
    shared_dir: str,
    *,
    playbook: str = "playbook.yml",
    tags: list[str] | None = None,
    skip_tags: list[str] | None = None,
    limit: list[str] | None = None,
    extra_vars: str | None = None,
) -> None:
    """Run a playbook against the byos static inventory over SSH.

    No HCLOUD_TOKEN and no dynamic inventory: the deploy key is decrypted from the
    vault into a private temp file passed via ``--private-key``.
    """
    working_dir = _resolve_working_dir(working_directory)
    env = _ansible_env(working_dir, shared_dir)

    with byos_private_key_file(
        working_directory, vault_password, shared_dir
    ) as key_path:
        command = _playbook_command(
            working_dir,
            playbook,
            "-i",
            BYOS_INVENTORY,
            "--private-key",
            key_path,
            *VAULT_PASSWORD_FROM_STDIN,
            *_playbook_options(
                tags=tags, skip_tags=skip_tags, limit=limit, extra_vars=extra_vars
            ),
        )
        _run_command(command, cwd=working_dir, env=env, input_text=vault_password)


def _dynamic_inventory_hostvars(
    working_dir: Path, shared_dir: str, env: dict[str, str]
) -> dict[str, dict]:
    """Return the hostvars of every host the Hetzner dynamic inventory resolves."""
    inventory_path = working_dir / "inventory.hcloud.yml"
    if not inventory_path.exists():
        inventory_path = working_dir / shared_dir / "inventory.hcloud.yml"
    if not inventory_path.exists():
        return {}
    result = _run_command(
        _uv_run(working_dir, "ansible-inventory", "-i", str(inventory_path), "--list"),
        cwd=working_dir,
        env=env,
        capture_output=True,
    )
    data = json.loads(result.stdout or "{}")
    return data.get("_meta", {}).get("hostvars") or {}


def _ensure_tailnet(
    working_dir: Path,
    environment: str,
    shared_dir: str,
    env: dict[str, str],
    *,
    hostvars: dict[str, dict] | None = None,
    strict: bool = True,
) -> None:
    """Check the tailnet before a run that reaches the servers of a private project.

    Public projects return before the inventory is even queried, so this costs
    them nothing.
    """
    if tailnet.resolve_network_mode(working_dir, environment) != "private":
        return
    if hostvars is None:
        hostvars = _dynamic_inventory_hostvars(working_dir, shared_dir, env)
    tailnet.ensure_tailnet_access(
        _resolve_project_name(working_dir),
        tailnet.private_hosts(hostvars),
        strict=strict,
    )


def _refresh_shared_roles(
    working_directory: str,
    shared_dir: str,
    version: str,
    repo_url: str | None,
    refresh: bool,
) -> Path:
    """The first step of every playbook command: pinned roles and collections."""
    return setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
    )


def _hcloud_env(
    working_directory: str, vault_password: str, environment: str, shared_dir: str
) -> dict[str, str]:
    """Ansible environment with the token the Hetzner dynamic inventory needs."""
    hcloud_token = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env = _ansible_env(_resolve_working_dir(working_directory), shared_dir)
    env["HCLOUD_TOKEN"] = hcloud_token
    return env


def _run_hcloud_playbook(
    working_directory: str,
    vault_password: str,
    environment: str,
    shared_dir: str,
    playbook: str,
    options: list[str],
    *,
    require_hosts: bool = False,
    strict_tailnet: bool = True,
) -> None:
    """Run a playbook against the servers the Hetzner dynamic inventory finds."""
    working_dir = _resolve_working_dir(working_directory)
    env = _hcloud_env(working_directory, vault_password, environment, shared_dir)
    hostvars = None
    if require_hosts:
        # Ansible skips every play on zero hosts, prints an empty PLAY RECAP
        # and exits 0. A deploy that overtakes the infrastructure provisioning
        # (both are triggered by the initial push) would report green while
        # having deployed nothing at all.
        hostvars = _dynamic_inventory_hostvars(working_dir, shared_dir, env)
        if not hostvars:
            raise click.ClickException(
                "The Hetzner inventory resolved to zero hosts — there is nothing to "
                "deploy to. Provision the servers first with "
                "`startup ansible infrastructure`, then run the deploy again."
            )
    _ensure_tailnet(
        working_dir,
        environment,
        shared_dir,
        env,
        hostvars=hostvars,
        strict=strict_tailnet,
    )
    _run_command(
        _playbook_command(working_dir, playbook, *VAULT_PASSWORD_FROM_STDIN, *options),
        cwd=working_dir,
        env=env,
        input_text=vault_password,
    )


def _run_server_playbook(
    working_directory: str,
    vault_password: str,
    environment: str,
    shared_dir: str,
    playbook: str,
    *,
    limit: str | None = None,
    extra_vars: str | None = None,
) -> None:
    """Run a playbook on the project's servers, byos or Hetzner alike."""
    if _is_byos(_resolve_working_dir(working_directory)):
        _run_byos_playbook(
            working_directory,
            vault_password,
            shared_dir,
            playbook=playbook,
            limit=[limit] if limit else None,
            extra_vars=extra_vars,
        )
        return
    _run_hcloud_playbook(
        working_directory,
        vault_password,
        environment,
        shared_dir,
        playbook,
        _playbook_options(limit=[limit] if limit else None, extra_vars=extra_vars),
    )


def run_validate(
    playbook: str,
    inventory: str,
    *,
    working_directory: str = ".",
    roles_path: str | None = None,
) -> None:
    """Check a local playbook without refreshing roles or resolving secrets."""
    working_dir = _resolve_working_dir(working_directory)
    for label, value in (("Playbook", playbook), ("Inventory", inventory)):
        if not (working_dir / value).is_file():
            raise click.ClickException(f"{label} file not found: {working_dir / value}")
    env = _ansible_env(working_dir)
    if roles_path:
        resolved_roles = (working_dir / roles_path).resolve()
        if not resolved_roles.is_dir():
            raise click.ClickException(f"Roles directory not found: {resolved_roles}")
        env["ANSIBLE_ROLES_PATH"] = str(resolved_roles)
    _run_command(
        _playbook_command(
            working_dir, playbook, "--inventory", inventory, "--syntax-check"
        ),
        cwd=working_dir,
        env=env,
    )


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
    extra_vars: dict[str, object] | None = None,
) -> None:
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    tags = [service or "all"]
    skip_tags = ["infrastructure"]
    encoded_vars = json.dumps(extra_vars) if extra_vars else None
    if _is_byos(_resolve_working_dir(working_directory)):
        _run_byos_playbook(
            working_directory,
            vault_password,
            shared_dir,
            tags=tags,
            skip_tags=skip_tags,
            extra_vars=encoded_vars,
        )
        return
    _run_hcloud_playbook(
        working_directory,
        vault_password,
        environment,
        shared_dir,
        "playbook.yml",
        _playbook_options(tags=tags, skip_tags=skip_tags, extra_vars=encoded_vars),
        require_hosts=True,
    )


def run_cert_manager_upgrade(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    cert_manager_version: str | None = None,
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    """Move the cluster's cert-manager to the pinned (or given) chart version.

    Unlike k3s-upgrade this runs the project's own playbook, not a shared one:
    the issuers depend on project settings such as cert_manager_solver, which
    live in group_vars and are invisible to a playbook in .shared-roles. A
    shared playbook would re-apply the issuers with the role defaults and
    silently switch a DNS-01 project back to HTTP-01.
    """
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(
        _resolve_working_dir(working_directory), "cert-manager-upgrade"
    )
    _validated_environment(environment)
    extra_vars: dict[str, object] = {"cert_manager_upgrade": True}
    if cert_manager_version:
        extra_vars["cert_manager_chart_version"] = cert_manager_version
    run_deploy(
        vault_password,
        environment,
        "cert-manager",
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        repo_url=repo_url,
        refresh=refresh,
        extra_vars=extra_vars,
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
    allow_worker_teardown: bool = False,
) -> None:
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(_resolve_working_dir(working_directory), "infrastructure")
    _validated_environment(environment)
    # Always override group_vars: destructive authorization is per invocation.
    teardown_vars = json.dumps({"allow_worker_teardown": allow_worker_teardown})
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    if _is_byos(_resolve_working_dir(working_directory)):
        # No cloud to provision — just install k3s + cluster add-ons on the VPS.
        # The provision-infrastructure host doesn't exist in the byos inventory,
        # so those plays are skipped automatically.
        _run_byos_playbook(
            working_directory,
            vault_password,
            shared_dir,
            tags=["infrastructure"],
            limit=[environment],
            extra_vars=teardown_vars,
        )
        return
    _run_hcloud_playbook(
        working_directory,
        vault_password,
        environment,
        shared_dir,
        "playbook.yml",
        _playbook_options(
            tags=["infrastructure"],
            limit=[environment, "provision-infrastructure"],
            extra_vars=teardown_vars,
        ),
        # This run may be the one that brings the servers onto the tailnet.
        strict_tailnet=False,
    )


def _print_firewalls(hcloud_token: str) -> None:
    """Print the project's Hetzner firewalls as JSON, read-only."""
    from hcloud import Client

    client = Client(
        token=hcloud_token,
        api_endpoint=os.environ.get("HCLOUD_ENDPOINT", "https://api.hetzner.cloud/v1"),
    )
    firewalls = [
        {
            "name": firewall.name,
            "id": firewall.id,
            "rules": [
                {
                    "direction": rule.direction,
                    "protocol": rule.protocol,
                    "port": rule.port,
                    "source_ips": rule.source_ips,
                }
                for rule in (firewall.rules or [])
            ],
            "servers": [
                resource.server.id
                for resource in (firewall.applied_to or [])
                if resource.server is not None
            ],
        }
        for firewall in client.firewalls.get_all()
    ]
    click.echo(json.dumps(firewalls, indent=2))


def run_firewall(
    working_directory: str,
    environment: str,
    *,
    dry_run: bool = True,
    repo_url: str | None = None,
    refresh: bool = True,
    version: str = DEFAULT_VERSION,
    inspect: bool = False,
) -> None:
    """Apply only the pinned firewall role, using the project's own group_vars."""
    from cli.cluster_commands import require_cluster_owner

    working_dir = _resolve_working_dir(working_directory)
    require_cluster_owner(working_dir, "firewall")
    _validated_environment(environment)
    if _is_byos(working_dir):
        raise click.ClickException(
            "Hetzner firewall changes must run from the cluster owner with a cloud inventory."
        )
    password = resolve_vault_password(None, working_directory)
    roles = setup_ansible(
        working_directory=working_directory,
        repo_url=repo_url,
        refresh=refresh,
        version=version,
    )
    env = _ansible_env(working_dir)
    env["HCLOUD_TOKEN"] = get_hcloud_token(working_directory, password, environment)
    if inspect:
        _print_firewalls(env["HCLOUD_TOKEN"])
        return
    env["ANSIBLE_ROLES_PATH"] = str(roles / "roles")
    inventories = []
    for name in ("inventory.hcloud.yml", "inventory.ini"):
        path = working_dir / name
        inventories += ["-i", str(path if path.exists() else roles / name)]
    # The playbook must sit beside group_vars, not inside the shared checkout.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", prefix=".startup-firewall-", dir=working_dir
    ) as stream:
        stream.write(yaml.safe_dump(playbook_inputs.firewall_play(roles)))
        stream.flush()
        _run_command(
            _playbook_command(
                working_dir,
                stream.name,
                *inventories,
                *VAULT_PASSWORD_FROM_STDIN,
                *(["--check", "--diff"] if dry_run else []),
            ),
            cwd=working_dir,
            env=env,
            input_text=password,
        )


def _read_inventory(
    working_dir: Path, inventory: str, shared_dir: str, env: dict[str, str]
) -> dict:
    inventory_path = working_dir / inventory
    if not inventory_path.exists():
        inventory_path = working_dir / shared_dir / inventory
    result = _run_command(
        _uv_run(working_dir, "ansible-inventory", "-i", str(inventory_path), "--list"),
        cwd=working_dir,
        env=env,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _master_address(
    inventory_data: dict, master_host: str | None, project_name: str
) -> str:
    """The address kubectl and scp use for the k3s master."""
    resolved_master_host = master_host or kubeconfig_files.extract_master_host(
        inventory_data
    )
    if not resolved_master_host:
        raise click.ClickException(
            "Could not auto-detect k3s master host from inventory. Specify --master-host."
        )

    hostvars = (
        inventory_data.get("_meta", {})
        .get("hostvars", {})
        .get(resolved_master_host, {})
    )
    if tailnet.is_private_network_host(hostvars):
        tailnet.ensure_tailnet_access(project_name, [resolved_master_host])

    master_ip = (
        playbook_inputs.normalize_inventory_value(hostvars.get("ansible_host"))
        or playbook_inputs.normalize_inventory_value(hostvars.get("public_ipv4"))
        or playbook_inputs.normalize_inventory_value(hostvars.get("public_ip"))
    )
    if not master_ip:
        raise click.ClickException(
            f"Could not resolve IP for host '{resolved_master_host}'."
        )
    return master_ip


@contextlib.contextmanager
def _ssh_identity_options(
    byos: bool,
    ssh_identity: str | None,
    working_directory: str,
    vault_password: str,
    shared_dir: str,
):
    """``-i`` options for scp/ssh; byos falls back to the vault's deploy key."""
    if ssh_identity:
        yield ["-i", ssh_identity]
    elif byos:
        with byos_private_key_file(
            working_directory, vault_password, shared_dir
        ) as key_path:
            yield ["-i", key_path]
    else:
        yield []


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
    direct: bool = False,
    ssh_identity: str | None = None,
    ssh_port: int = 22,
) -> Path:
    """Fetch the cluster's kubeconfig from the master and import it as a context."""
    from cli.kube_access import configure_access, private_write

    working_dir = _resolve_working_dir(working_directory)
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    byos = _is_byos(working_dir)
    project_name = _resolve_project_name(working_dir)
    k8s_namespace = _resolve_k8s_namespace(working_dir)
    if byos:
        # No Hetzner API; read the cluster's kubeconfig over SSH from the VPS.
        env = _ansible_env(working_dir, shared_dir)
        inventory = BYOS_INVENTORY
    else:
        env = _hcloud_env(working_directory, vault_password, environment, shared_dir)

    master_ip = _master_address(
        _read_inventory(working_dir, inventory, shared_dir, env),
        master_host,
        project_name,
    )
    output_path = kubeconfig_files.output_path(out, project_name, environment, byos)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        with _ssh_identity_options(
            byos, ssh_identity, working_directory, vault_password, shared_dir
        ) as identity_options:
            _run_command(
                [
                    "scp",
                    *kubeconfig_files.ssh_options(ssh_port, identity_options, "-P"),
                    f"{ssh_user}@{master_ip}:/etc/rancher/k3s/k3s.yaml",
                    str(tmp_path),
                ],
                cwd=working_dir,
            )
            if context_name:
                context = context_name
            elif byos:
                context = (
                    f"{project_name}-{environment}" if env_suffix else project_name
                )
            else:
                remote_name = (
                    _run_command(
                        [
                            "ssh",
                            *kubeconfig_files.ssh_options(
                                ssh_port, identity_options, "-p"
                            ),
                            f"{ssh_user}@{master_ip}",
                            "hostname",
                        ],
                        cwd=working_dir,
                        capture_output=True,
                    )
                    .stdout.splitlines()[0]
                    .strip()
                )
                context = kubeconfig_files.derive_context_name(
                    remote_name, environment, env_suffix
                )

        kubeconfig = yaml.safe_load(tmp_path.read_text(encoding="utf-8"))
        # In private network mode master_ip is the node's MagicDNS name. That is
        # also its k3s node name, which k3s always puts into the API
        # certificate, so the name verifies without any extra SAN.
        for cluster in kubeconfig.get("clusters", []):
            if cluster.get("cluster", {}).get("server"):
                cluster["cluster"]["server"] = f"https://{master_ip}:6443"
        _configure_kubeconfig_context(kubeconfig, context, k8s_namespace)

        if not direct:
            kubeconfig = configure_access(
                kubeconfig,
                context,
                master_ip,
                ssh_user,
                ssh_port,
                identity=ssh_identity,
            )
            # Do not import redundant direct credentials alongside the managed user.
            kubeconfig["clusters"] = kubeconfig["clusters"][-1:]
            kubeconfig["users"] = kubeconfig["users"][-1:]

        private_write(output_path, kubeconfig)
        if not shutil.which("kubectl"):
            raise click.ClickException(
                "kubectl not found in PATH. Please install kubectl."
            )
        kubeconfig_files.merge_into_kube_config(kubeconfig, context, make_current)
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
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)

    playbook_path = playbook_inputs.resolve_playbook_path(
        working_dir, playbook, "Backup", shared_dir
    )
    project_name = _resolve_project_name(working_dir)
    resolved_backup_dir = (
        Path(backup_dir).expanduser()
        if backup_dir
        else Path.home() / "Backups" / project_name
    )
    # k8s_namespace is deliberately not passed here. Ansible resolves it from
    # `group_vars/`, and an --extra-vars copy would outrank that - so a project
    # that sets the namespace anywhere but the top of `all.yml` would deploy
    # into one namespace and back up from another.
    backup_extra_vars = (
        f"project_name={project_name} "
        f"backup_environment={environment} "
        f"local_backup_root={resolved_backup_dir}"
    )
    with playbook_inputs.project_data_playbook(
        working_dir, playbook_path, environment
    ) as playbook_path:
        _run_server_playbook(
            working_directory,
            vault_password,
            environment,
            shared_dir,
            str(playbook_path),
            extra_vars=backup_extra_vars,
        )


def run_dns(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    playbook: str = "dns-playbook.yml",
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    """Point the project's domains at its cluster through Hetzner DNS only.

    Touches no server, so startups attached to a shared cluster may run it with
    the DNS token in their own Vault.
    """
    _validated_environment(environment)
    working_dir = _resolve_working_dir(working_directory)
    target_ip = playbook_inputs.dns_target_ip(working_dir)
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    playbook_path = playbook_inputs.resolve_playbook_path(
        working_dir, playbook, "DNS", shared_dir
    )
    env = _hcloud_env(working_directory, vault_password, environment, shared_dir)
    extra_vars = {
        "dns_target_ip": target_ip,
        "ansible_python_interpreter": "{{ ansible_playbook_python }}",
    }
    with playbook_inputs.project_data_playbook(
        working_dir, playbook_path, environment
    ) as playbook_path:
        _run_command(
            _playbook_command(
                working_dir,
                str(playbook_path),
                "-i",
                "localhost,",
                "--connection",
                "local",
                *VAULT_PASSWORD_FROM_STDIN,
                "--extra-vars",
                json.dumps(extra_vars),
            ),
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
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(_resolve_working_dir(working_directory), "update-vms")
    _validated_environment(environment)
    working_dir = _resolve_working_dir(working_directory)
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    playbook_path = playbook_inputs.resolve_playbook_path(
        working_dir, playbook, "Update VMs", shared_dir
    )
    _run_server_playbook(
        working_directory,
        vault_password,
        environment,
        shared_dir,
        str(playbook_path),
        limit=f"{environment},{limit}" if limit else environment,
        extra_vars=json.dumps(
            {"update_reboot": reboot, "update_environment": environment}
        ),
    )


def run_os_upgrade(
    vault_password: str | None,
    environment: str,
    *,
    target_version: str,
    execute: bool = False,
    backup_confirmed: bool = False,
    health_urls: tuple[str, ...] = (),
    working_directory: str = ".",
    limit: str | None = None,
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    """Preview or perform a serial Ubuntu LTS release upgrade."""
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(_resolve_working_dir(working_directory), "os-upgrade")
    _validated_environment(environment)
    playbook_inputs.validate_os_upgrade_options(
        target_version, execute, backup_confirmed, health_urls, limit
    )
    working_dir = _resolve_working_dir(working_directory)
    vault_password = resolve_vault_password(vault_password, working_directory)
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    # Always use the reviewed shared playbook, rather than a same-named local file.
    playbook_path = working_dir / shared_dir / "os-upgrade-playbook.yml"
    if not playbook_path.is_file():
        raise click.ClickException(
            "OS upgrade playbook missing from the pinned shared roles; sync a release containing it and update the reviewed role pin"
        )
    effective_limit = f"{environment}:&{limit}" if limit else environment
    extra_vars = json.dumps(
        {
            "os_upgrade_target": target_version,
            "os_upgrade_environment": environment,
            "os_upgrade_execute": execute,
            "os_upgrade_backup_confirmed": backup_confirmed,
            "os_upgrade_health_urls": list(health_urls),
        }
    )
    click.echo(
        "OS upgrade: "
        + (
            "execute (nodes may be unavailable during reboot)"
            if execute
            else "preview only; no package upgrades or reboots"
        )
    )
    if _is_byos(working_dir):
        with byos_private_key_file(
            working_directory, vault_password, shared_dir
        ) as key_path:
            _run_os_upgrade_playbook(
                working_dir,
                vault_password,
                _ansible_env(working_dir, shared_dir),
                playbook_path,
                effective_limit,
                extra_vars,
                inventory_args=["-i", BYOS_INVENTORY, "--private-key", key_path],
            )
        return
    env = _hcloud_env(working_directory, vault_password, environment, shared_dir)
    _ensure_tailnet(working_dir, environment, shared_dir, env)
    _run_os_upgrade_playbook(
        working_dir,
        vault_password,
        env,
        playbook_path,
        effective_limit,
        extra_vars,
    )


def _run_os_upgrade_playbook(
    working_dir: Path,
    vault_password: str,
    env: dict[str, str],
    playbook_path: Path,
    effective_limit: str,
    extra_vars: str,
    *,
    inventory_args: list[str] | None = None,
) -> None:
    command = _playbook_command(
        working_dir,
        str(playbook_path),
        *(inventory_args or []),
        *VAULT_PASSWORD_FROM_STDIN,
        "-l",
        effective_limit,
        "--extra-vars",
        extra_vars,
    )
    try:
        listing = _run_command(
            [*command, "--list-hosts"],
            cwd=working_dir,
            env=env,
            input_text=vault_password,
            capture_output=True,
        )
    except click.ClickException as exc:
        if "no hosts to target" in str(exc):
            raise click.ClickException(
                "No VM hosts match this environment and --limit; no upgrade started"
            ) from exc
        raise
    if not any(int(count) for count in re.findall(r"hosts \((\d+)\)", listing.stdout)):
        raise click.ClickException(
            "No VM hosts match this environment and --limit; no upgrade started"
        )
    _run_command(command, cwd=working_dir, env=env, input_text=vault_password)


def run_k3s_upgrade(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    playbook: str = "k3s-upgrade-playbook.yml",
    k3s_version: str | None = None,
    limit: str | None = None,
    shared_dir: str = DEFAULT_SHARED_DIR,
    version: str = DEFAULT_VERSION,
    repo_url: str | None = None,
    refresh: bool = True,
) -> None:
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(_resolve_working_dir(working_directory), "k3s-upgrade")
    _validated_environment(environment)
    working_dir = _resolve_working_dir(working_directory)
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    playbook_path = playbook_inputs.resolve_playbook_path(
        working_dir, playbook, "k3s upgrade", shared_dir
    )
    extra_vars: dict[str, object] = {"k3s_upgrade": True}
    if k3s_version:
        extra_vars["k3s_version"] = k3s_version
    _run_server_playbook(
        working_directory,
        vault_password,
        environment,
        shared_dir,
        str(playbook_path),
        limit=f"{environment},{limit}" if limit else environment,
        extra_vars=json.dumps(extra_vars),
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
    _refresh_shared_roles(working_directory, shared_dir, version, repo_url, refresh)
    playbook_path = playbook_inputs.resolve_playbook_path(
        working_dir, playbook, "Restore", shared_dir
    )
    project_name = _resolve_project_name(working_dir)
    search_root = (
        Path(backup_dir).expanduser().resolve()
        if backup_dir
        else (Path.home() / "Backups" / project_name).resolve()
    )
    if not search_root.exists():
        raise click.ClickException(f"Backup directory not found: {search_root}")
    extra_vars = playbook_inputs.restore_extra_vars(
        project_name,
        environment,
        search_root,
        db_file=db_file,
        media_file=media_file,
        restore_db=restore_db,
        restore_media=restore_media,
    )
    with playbook_inputs.project_data_playbook(
        working_dir, playbook_path, environment
    ) as playbook_path:
        _run_server_playbook(
            working_directory,
            vault_password,
            environment,
            shared_dir,
            str(playbook_path),
            extra_vars=json.dumps(extra_vars),
        )
