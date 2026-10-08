"""Explicit cluster ownership and public connection descriptors."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import click
import yaml

from cli import ansible_commands as ansible

CLUSTER_FILE = "cluster.yml"


def read_descriptor(path: Path) -> dict:
    """Accept only public connection settings, never arbitrary inventory vars."""
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException("Cannot read the cluster descriptor.") from exc
    if not isinstance(data, dict) or set(data) != {
        "schema",
        "id",
        "owner",
        "environment",
        "nodes",
    }:
        raise click.ClickException("Invalid cluster descriptor fields.")
    if data["schema"] != 1 or data["environment"] != "production":
        raise click.ClickException(
            "Cluster descriptor requires schema 1 and production."
        )
    if not isinstance(data["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", data["id"]):
        raise click.ClickException("Invalid cluster identity.")
    if not isinstance(data["owner"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data["owner"]
    ):
        raise click.ClickException("Invalid cluster owner repository.")
    nodes = data["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise click.ClickException("Cluster descriptor needs at least one master node.")
    seen = set()
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {
            "name",
            "host",
            "ssh_user",
            "role",
        }:
            raise click.ClickException("Invalid cluster node fields.")
        for key in ("name", "host", "ssh_user"):
            if (
                not isinstance(node[key], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]+", node[key])
                or node[key].startswith("-")
            ):
                raise click.ClickException(f"Invalid cluster node {key}.")
        if node["role"] not in ("master", "worker") or node["name"] in seen:
            raise click.ClickException("Invalid or duplicate cluster node.")
        seen.add(node["name"])
    if not any(node["role"] == "master" for node in nodes):
        raise click.ClickException("Cluster descriptor needs at least one master node.")
    return data


def require_cluster_owner(working_dir: Path, operation: str) -> None:
    """Reject cluster mutations before credentials, setup or remote commands."""
    path = working_dir / CLUSTER_FILE
    if not path.exists():
        return
    try:
        settings = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException(
            "Cannot read cluster ownership configuration."
        ) from exc
    if not isinstance(settings, dict) or settings.get("managed") is not True:
        owner = (
            settings.get("owner", "the cluster owner")
            if isinstance(settings, dict)
            else "the cluster owner"
        )
        raise click.ClickException(
            f"{operation} changes the shared cluster; run it from {owner}."
        )


def configure_project_cluster(ctx) -> None:
    """Write public ownership vars and a static inventory for attached projects."""
    if not ctx.shared_cluster and not ctx.cluster_config:
        return
    path = ctx.deployment_dir / CLUSTER_FILE
    if path.exists():
        existing = yaml.safe_load(path.read_text())
        expected_id = ctx.cluster_config["id"] if ctx.cluster_config else existing["id"]
        if existing["id"] != expected_id or existing["managed"] != (
            ctx.cluster_config is None
        ):
            raise click.ClickException(
                "Project already belongs to another cluster or ownership mode."
            )
        return
    descriptor: dict = ctx.cluster_config or {
        "schema": 1,
        "id": uuid.uuid4().hex,
        "owner": ctx.full_repo,
        "environment": "production",
        "nodes": [],
    }
    managed = ctx.cluster_config is None
    settings = {**descriptor, "managed": managed}
    path.write_text(yaml.safe_dump(settings, sort_keys=False))
    variables = {
        "startup_cluster_id": descriptor["id"],
        "startup_cluster_owner": descriptor["owner"],
        "startup_cluster_managed": managed,
        "startup_project_owner": ctx.full_repo,
        "startup_namespace_policy": True,
    }
    (ctx.deployment_dir / "group_vars" / "cluster.yml").write_text(
        yaml.safe_dump(variables, sort_keys=False)
    )
    if managed:
        return
    groups = {}
    for role in ("master", "worker"):
        hosts = {
            node["name"]: {
                "ansible_host": node["host"],
                "ansible_user": node["ssh_user"],
                "hcloud_labels": {"type": role},
            }
            for node in descriptor["nodes"]
            if node["role"] == role
        }
        if hosts:
            groups[f"hcloud_type_{role}"] = {"hosts": hosts}
    inventory = {
        "all": {
            "children": {
                "production": {"children": {"hcloud": {}}},
                "hcloud": {"children": groups},
            }
        }
    }
    (ctx.deployment_dir / ansible.BYOS_INVENTORY).write_text(
        yaml.safe_dump(inventory, sort_keys=False)
    )


@click.group()
def cluster():
    """Connect independently deployed startups to an owner-managed k3s cluster."""


@cluster.command("export")
@click.option("--working-directory", default="deployment", show_default=True)
@click.option("--environment", type=click.Choice(["production"]), default="production")
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--repo-url", default=None)
@click.option("--version", default="main")
@click.option("--refresh/--no-refresh", default=True)
def export_cluster(working_directory, environment, output, repo_url, version, refresh):
    """Export public node addresses from the cluster owner's inventory.

    Bootstrap the owner with --shared-cluster first. The descriptor contains no
    credentials. Attached startups get their own Vault and SSH deployment key.
    """
    working_dir = Path(working_directory).expanduser().resolve()
    require_cluster_owner(working_dir, "cluster export")
    marker = working_dir / CLUSTER_FILE
    if not marker.exists():
        raise click.ClickException(
            "Bootstrap the cluster owner with --shared-cluster first."
        )
    if output.exists():
        raise click.ClickException(
            "Output already exists; choose a new descriptor path."
        )
    settings = yaml.safe_load(marker.read_text())
    password = ansible.resolve_vault_password(None, working_directory)
    ansible.setup_ansible(
        working_directory=working_directory,
        repo_url=repo_url,
        version=version,
        refresh=refresh,
    )
    env = ansible._ansible_env(working_dir, ansible.DEFAULT_SHARED_DIR)
    if ansible._is_byos(working_dir):
        inventory = working_dir / ansible.BYOS_INVENTORY
    else:
        env["HCLOUD_TOKEN"] = ansible.get_hcloud_token(
            working_directory, password, environment
        )
        inventory = working_dir / "inventory.hcloud.yml"
    result = ansible._run_command(
        [
            ansible._find_uv(),
            "run",
            "--project",
            str(working_dir),
            "ansible-inventory",
            "-i",
            str(inventory),
            "--list",
            "--vault-password-file",
            "/bin/cat",
        ],
        cwd=working_dir,
        env=env,
        capture_output=True,
        input_text=password,
    )
    hostvars = json.loads(result.stdout)["_meta"]["hostvars"]
    nodes = []
    for name, values in sorted(hostvars.items()):
        labels = values.get("hcloud_labels", {})
        role = labels.get("type")
        if role not in ("master", "worker"):
            continue
        if labels.get("network") == "private":
            raise click.ClickException(
                "Shared-cluster bootstrap currently requires public SSH access."
            )
        if not ansible._is_byos(working_dir) and not name.startswith(
            ansible._resolve_project_name(working_dir) + "-"
        ):
            continue
        nodes.append(
            {
                "name": name,
                "host": values["ansible_host"],
                "ssh_user": values.get("ansible_user", "root"),
                "role": role,
            }
        )
    descriptor = {
        key: settings[key] for key in ("schema", "id", "owner", "environment")
    }
    descriptor["nodes"] = nodes
    # Validate before publishing; do not export hostvars, tokens or kubeconfigs.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_text(yaml.safe_dump(descriptor, sort_keys=False))
        read_descriptor(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    click.echo(f"Public cluster descriptor written to {output.resolve()}")
