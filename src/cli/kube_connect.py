"""Connect Kubernetes clients over SSH without storing client private keys."""

from __future__ import annotations

import os
import re
from pathlib import Path

import click

from cli.kube_access import access_lock, configure_access, private_write
from cli.kube_credentials import fetch_kubeconfig, read_state


@click.command("connect")
@click.option(
    "--context", "context_name", required=True, help="Local Kubernetes context name."
)
@click.option("--host", required=True, help="SSH address of the cluster master.")
@click.option("--ssh-user", default="root", show_default=True)
@click.option(
    "--ssh-port", type=click.IntRange(1, 65535), default=22, show_default=True
)
@click.option(
    "--identity-file", type=click.Path(exists=True, dir_okay=False, resolve_path=True)
)
@click.option(
    "--tls-server-name",
    default=None,
    help="Expected name in the API certificate; defaults to host.",
)
@click.option(
    "--namespace",
    default=None,
    help="Preserve the existing namespace unless explicitly set.",
)
@click.option(
    "--kubeconfig", "config_path", type=click.Path(path_type=Path), default=None
)
def connect(
    context_name,
    host,
    ssh_user,
    ssh_port,
    identity_file,
    tls_server_name,
    namespace,
    config_path,
):
    """Fetch Kubernetes credentials over SSH and enable automatic local tunnels.

    Uses your SSH agent or --identity-file. Trust the server's host key with SSH
    first. Public API access is unnecessary; firewall changes are separate.
    """
    if config_path is None:
        configured = os.environ.get("KUBECONFIG", "")
        if os.pathsep in configured:
            raise click.ClickException(
                "Multiple KUBECONFIG files: select one with --kubeconfig."
            )
        config_path = (
            Path(configured) if configured else Path.home() / ".kube" / "config"
        )
    config_path = config_path.expanduser().resolve()
    for value in (host, ssh_user):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or value.startswith("-"):
            raise click.ClickException("Invalid SSH host or user.")
    connection = {
        "host": host,
        "ssh_user": ssh_user,
        "ssh_port": ssh_port,
        "identity": identity_file,
    }
    try:
        source = fetch_kubeconfig(connection)
        source["contexts"][0]["name"] = context_name
        source["current-context"] = context_name
        # k3s exports one context. Use context-specific names when merging.
        source["clusters"][0]["name"] = context_name
        source["users"][0]["name"] = context_name
        source["contexts"][0]["context"].update(cluster=context_name, user=context_name)
    except (KeyError, IndexError, TypeError):
        raise click.ClickException("SSH returned an invalid k3s kubeconfig.") from None
    with access_lock(config_path.with_name(config_path.name + ".startup.lock")):
        existing: dict = (
            read_state(config_path)
            if config_path.exists()
            else {
                "apiVersion": "v1",
                "kind": "Config",
                "contexts": [],
                "clusters": [],
                "users": [],
            }
        )
        old = next((c for c in existing["contexts"] if c["name"] == context_name), None)
        source["contexts"][0]["context"]["namespace"] = namespace or (old or {}).get(
            "context", {}
        ).get("namespace", "default")
        managed = configure_access(
            source,
            context_name,
            host,
            ssh_user,
            ssh_port,
            identity_file,
            tls_server_name,
        )
        for kind in ("contexts", "clusters", "users"):
            # Only import the managed cluster/user, not the unmodified source.
            entries = managed[kind] if kind == "contexts" else managed[kind][-1:]
            replacements = {entry["name"]: entry for entry in entries}
            existing[kind] = [
                replacements.pop(e["name"], e) for e in existing[kind]
            ] + list(replacements.values())
        if not existing.get("current-context"):
            existing["current-context"] = context_name
        private_write(config_path, existing)
    click.echo(
        f"SSH access ready: {context_name} (namespace {source['contexts'][0]['context']['namespace']})."
    )
