"""Validated inputs for the ``startup ansible`` playbook commands.

Everything here only reads project files and builds playbooks or extra-vars;
running Ansible stays in ``ansible_commands``.
"""

from __future__ import annotations

import contextlib
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import click
import yaml

# A project is bring-your-own-server (or attached to a shared cluster) when it
# ships this static inventory instead of using the Hetzner dynamic one.
BYOS_INVENTORY = "inventory.byos.yml"


def normalize_inventory_value(value: object) -> str | None:
    """An inventory value as text; ansible-inventory wraps some in ``__ansible_unsafe``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        unsafe_value = value.get("__ansible_unsafe")
        if isinstance(unsafe_value, str):
            return unsafe_value
    return str(value)


def resolve_playbook_path(
    working_dir: Path, playbook: str, label: str, shared_dir: str
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


@contextlib.contextmanager
def project_data_playbook(
    working_dir: Path, playbook: Path, environment: str = "production"
):
    """Import data-operation playbooks from the project's group_vars root."""
    if playbook.parent == working_dir:
        yield playbook
        return
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix=".startup-data-",
        suffix=".yml",
        dir=working_dir,
        delete=False,
    ) as wrapper:
        tasks = [
            {"ansible.builtin.include_vars": {"file": str(path)}, "no_log": True}
            for path in (
                working_dir / "group_vars" / "all.yml",
                working_dir / "group_vars" / f"{environment}.yml",
            )
            if path.exists()
        ]
        wrapper.write(
            yaml.safe_dump(
                [
                    {"hosts": "all", "gather_facts": False, "tasks": tasks},
                    {"import_playbook": str(playbook)},
                ]
            )
        )
        wrapper_path = Path(wrapper.name)
    try:
        yield wrapper_path
    finally:
        wrapper_path.unlink(missing_ok=True)


def dns_target_ip(working_dir: Path) -> str:
    """The public address of the first master in the project's static inventory."""
    inventory_path = working_dir / BYOS_INVENTORY
    if not inventory_path.exists():
        raise click.ClickException(
            "startup ansible dns needs a static inventory (shared-cluster attachment "
            "or own server). Hetzner-provisioned projects manage DNS through "
            "startup ansible infrastructure."
        )
    inventory = yaml.safe_load(inventory_path.read_text()) or {}

    def masters(group: dict) -> list[str]:
        found = []
        for name, child_group in (group.get("children") or {}).items():
            child = child_group or {}
            if name == "hcloud_type_master":
                found += [
                    str(host.get("ansible_host") or host_name)
                    for host_name, host in (child.get("hosts") or {}).items()
                ]
            found += masters(child)
        return found

    hosts = masters(inventory.get("all") or {})
    if not hosts:
        raise click.ClickException(f"No master node in {inventory_path}.")
    return hosts[0]


def firewall_play(roles: Path) -> list[dict]:
    """A play that applies only the hetzner-firewall role, provisioning nothing."""
    return [
        {
            "hosts": "provision-infrastructure",
            "connection": "local",
            "gather_facts": False,
            "pre_tasks": [
                {
                    "name": "Read pinned network defaults without provisioning",
                    "ansible.builtin.include_vars": {
                        "file": str(roles / "roles/hetzner-network/defaults/main.yml"),
                        "name": "startup_network_defaults",
                    },
                },
                {
                    "name": "Use the pinned network range when the project has no override",
                    "ansible.builtin.set_fact": {
                        "network_ip_range": "{{ startup_network_defaults.network_ip_range }}",
                    },
                    "when": "network_ip_range is not defined",
                },
            ],
            "roles": ["hetzner-firewall"],
        }
    ]


def validate_os_upgrade_options(
    target_version: str,
    execute: bool,
    backup_confirmed: bool,
    health_urls: tuple[str, ...],
    limit: str | None,
) -> None:
    if not re.fullmatch(r"[0-9]{2}\.04", target_version) or int(target_version[:2]) % 2:
        raise click.ClickException(
            "--target-version must be an Ubuntu LTS version such as 26.04"
        )
    if execute and not backup_confirmed:
        raise click.ClickException(
            "--execute requires --backup-confirmed after verifying backups and a recovery plan"
        )
    if execute and not health_urls:
        raise click.ClickException(
            "--execute requires at least one --health-url for application verification"
        )
    if limit and not re.fullmatch(r"[A-Za-z0-9_*?.-]+", limit):
        raise click.ClickException(
            "--limit must be a single host, group or glob within the environment"
        )
    for url in health_urls:
        # These URLs are printed by Ansible; never accept embedded credentials.
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise click.ClickException(
                "--health-url must be a valid HTTPS URL"
            ) from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise click.ClickException(
                "--health-url must be an HTTPS URL without embedded credentials"
            )


def restore_extra_vars(
    project_name: str,
    environment: str,
    search_root: Path,
    *,
    db_file: str | None,
    media_file: str | None,
    restore_db: bool,
    restore_media: bool,
) -> dict[str, object]:
    """Resolve the backup files to restore; the newest match unless one is given."""
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
    # See the note in the backup command: the namespace comes from group_vars,
    # not from an --extra-vars copy that would outrank it.
    return {
        "project_name": project_name,
        "restore_environment": environment,
        "restore_db": restore_db,
        "restore_media": restore_media,
        "db_backup_file": str(resolved_db_file) if resolved_db_file else "",
        "media_backup_file": str(resolved_media_file) if resolved_media_file else "",
    }
