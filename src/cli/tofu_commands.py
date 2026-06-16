"""OpenTofu provisioning step.

Replaces the imperative `provision-infrastructure` Ansible play (the hetzner-*
provisioning roles). Ansible still owns configuration (k3s, Helm, cert-manager,
CCM, CSI) and discovers the nodes OpenTofu created via inventory.hcloud.yml.

Tofu variables are sourced from the existing Ansible group_vars so there is a
single source of truth — no config duplication. The same HCLOUD_TOKEN drives
compute and DNS (unified Cloud API).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import click
import yaml

from .ansible_bin import ansible_bin
from .ansible_commands import (
    DEFAULT_SHARED_DIR,
    _ansible_env,
    _find_uv,
    _resolve_working_dir,
    _run_command,
    get_hcloud_token,
)

DEFAULT_TOFU_DIR = "tofu"

# Minimum OpenTofu version the CLI requires. 1.10 introduced native S3 state
# locking (use_lockfile), which the remote backend relies on. Bump this here
# (and the CI `tofu_version`) to raise the floor.
MIN_TOFU_VERSION = (1, 10, 0)

# Cross-platform install/upgrade reference (Linux/macOS/Windows) — not brew-only.
TOFU_INSTALL_URL = "https://opentofu.org/docs/intro/install/"


class _GroupVarsLoader(yaml.SafeLoader):
    """SafeLoader that ignores Ansible's !vault tags (secrets aren't needed here)."""


_GroupVarsLoader.add_constructor("!vault", lambda loader, node: None)


# tofu variable name -> group_vars key. Keeps group_vars the single source of truth.
_TOFU_VAR_MAP = {
    "project_name": "project_name",
    "base_domain": "base_domain",
    "additional_domains": "additional_domains",
    "ssh_public_keys": "ssh_public_keys",
    "create_load_balancer": "create_load_balancer",
    "master_count": "master_count",
    "worker_count": "worker_count",
    "server_type": "server_type",
    "location": "location",
}


def _version_str(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _parse_tofu_version(output: str) -> tuple[int, int, int] | None:
    """Pull the X.Y.Z version out of `tofu version` output."""
    match = re.search(r"v(\d+)\.(\d+)\.(\d+)", output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _find_tofu() -> str:
    """Locate the OpenTofu binary and verify it meets the minimum version."""
    tofu = shutil.which("tofu") or shutil.which("terraform")
    if not tofu:
        raise click.ClickException(
            f"OpenTofu (tofu) not found on PATH. Install OpenTofu "
            f">= {_version_str(MIN_TOFU_VERSION)}: {TOFU_INSTALL_URL}"
        )

    result = _run_command([tofu, "version"], cwd=Path.cwd(), capture_output=True)
    version = _parse_tofu_version(result.stdout)
    if version is None:
        raise click.ClickException(
            f"Could not determine the OpenTofu version from `{tofu} version`."
        )
    if version < MIN_TOFU_VERSION:
        raise click.ClickException(
            f"OpenTofu >= {_version_str(MIN_TOFU_VERSION)} is required, "
            f"but {_version_str(version)} is installed. Update it: {TOFU_INSTALL_URL}"
        )
    return tofu


def _load_group_vars(working_dir: Path, environment: str) -> dict:
    group_vars = working_dir / "group_vars"
    merged: dict = {}
    for name in ("all.yml", f"{environment}.yml"):
        path = group_vars / name
        if path.exists():
            loaded = yaml.load(path.read_text(), Loader=_GroupVarsLoader) or {}
            if isinstance(loaded, dict):
                merged.update(loaded)
    return merged


def _tofu_var_env(group_vars: dict) -> dict[str, str]:
    """Translate group_vars into TF_VAR_* environment variables."""
    env: dict[str, str] = {}
    for tf_name, gv_key in _TOFU_VAR_MAP.items():
        value = group_vars.get(gv_key)
        if value is None:
            continue
        # TF_VAR_ accepts JSON for both complex and scalar values.
        env[f"TF_VAR_{tf_name}"] = value if isinstance(value, str) else json.dumps(value)
    if "TF_VAR_project_name" not in env:
        raise click.ClickException("group_vars is missing 'project_name' for OpenTofu.")
    if "TF_VAR_base_domain" not in env:
        raise click.ClickException("group_vars is missing 'base_domain' for OpenTofu.")
    return env


def _state_backend_env(
    working_directory: str,
    vault_password: str,
    environment: str,
    shared_dir: str,
) -> dict[str, str]:
    """Read optional object-storage credentials for the remote S3 state backend.

    Returns an empty dict (-> local state) if the vault entries are absent, so a
    project can iterate before a state bucket exists.
    """
    access_key = _try_vault_view(
        working_directory, vault_password, f"tfstate_s3_access_key_{environment}", shared_dir
    )
    secret_key = _try_vault_view(
        working_directory, vault_password, f"tfstate_s3_secret_key_{environment}", shared_dir
    )
    if access_key and secret_key:
        return {
            "AWS_ACCESS_KEY_ID": access_key,
            "AWS_SECRET_ACCESS_KEY": secret_key,
        }
    return {}


def _try_vault_view(
    working_directory: str,
    vault_password: str,
    name: str,
    shared_dir: str,
) -> str | None:
    working_dir = _resolve_working_dir(working_directory)
    if not (working_dir / name).exists():
        return None
    env = _ansible_env(working_dir, shared_dir)
    try:
        result = _run_command(
            [
                _find_uv(),
                "run",
                "--project",
                str(working_dir),
                ansible_bin("ansible-vault"),
                "view",
                name,
                "--vault-password-file",
                "/bin/cat",
            ],
            cwd=working_dir,
            env=env,
            input_text=vault_password,
            capture_output=True,
        )
    except click.ClickException:
        return None
    return result.stdout.strip() or None


def run_tofu_provision(
    vault_password: str,
    environment: str,
    *,
    working_directory: str = ".",
    shared_dir: str = DEFAULT_SHARED_DIR,
    tofu_dir: str = DEFAULT_TOFU_DIR,
) -> None:
    """Provision infrastructure with OpenTofu.

    Assumes the shared roles/tofu modules are already set up (the caller runs
    setup_ansible). Uses local state unless a backend.<env>.hcl is present.
    """
    working_dir = _resolve_working_dir(working_directory)
    tofu_path = working_dir / tofu_dir
    if not (tofu_path / "main.tf").exists():
        raise click.ClickException(
            f"No OpenTofu config found at '{tofu_path}'. "
            "Re-bootstrap the project or add a deployment/tofu/ directory."
        )

    env = _ansible_env(working_dir, shared_dir)
    env["HCLOUD_TOKEN"] = get_hcloud_token(
        working_directory, vault_password, environment, shared_dir
    )
    env.update(
        _state_backend_env(working_directory, vault_password, environment, shared_dir)
    )
    env.update(_tofu_var_env(_load_group_vars(working_dir, environment)))

    tofu = _find_tofu()
    backend_config = tofu_path / f"backend.{environment}.hcl"
    init_cmd = [tofu, "init", "-input=false"]
    if backend_config.exists():
        click.echo(f"OpenTofu: remote state via {backend_config.name}")
        init_cmd += ["-backend-config", str(backend_config), "-reconfigure"]
    else:
        click.echo("OpenTofu: no backend.<env>.hcl found — using local state.")
        init_cmd += ["-backend=false"]

    click.echo("Provisioning infrastructure with OpenTofu ...")
    _run_command(init_cmd, cwd=tofu_path, env=env)
    _run_command(
        [tofu, "apply", "-input=false", "-auto-approve"],
        cwd=tofu_path,
        env=env,
    )
