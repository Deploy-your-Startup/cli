"""Public, allowlisted diagnostics for managed Kubernetes connections."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from cli.kube_credentials import read_state


def connection_state(config_path: Path, context_name: str) -> tuple[Path, dict]:
    config = read_state(config_path)
    try:
        selected = next(
            c["context"] for c in config["contexts"] if c["name"] == context_name
        )
        user = next(u["user"] for u in config["users"] if u["name"] == selected["user"])
        helper = user["exec"]
        args = helper["args"]
        if args[:3] != ["-I", "-m", "cli.kube_access"] or len(args) != 4:
            raise ValueError
        return Path(args[3]), selected
    except (KeyError, TypeError, ValueError, StopIteration):
        raise click.ClickException(
            "Select a startup-managed Kubernetes context."
        ) from None


@click.command("inspect")
@click.option("--context", "context_name", required=True)
@click.option("--kubeconfig", "config_path", type=click.Path(path_type=Path))
def inspect_connection(context_name: str, config_path: Path | None) -> None:
    """Show safe connection metadata; never print kubeconfigs or credentials."""
    configured = os.environ.get("KUBECONFIG", "")
    if config_path is None and os.pathsep in configured:
        raise click.ClickException("Select one file with --kubeconfig.")
    config_path = config_path or (
        Path(configured) if configured else Path.home() / ".kube/config"
    )
    path, selected = connection_state(config_path.expanduser(), context_name)
    data = read_state(path)
    fields = {key: data.get(key) for key in ("host", "ssh_user", "ssh_port")}
    if any(isinstance(value, (dict, list)) for value in fields.values()):
        raise click.ClickException("Invalid Kubernetes connection metadata.")
    click.echo(
        json.dumps(
            {
                "context": context_name,
                **fields,
                "namespace": selected.get("namespace", "default"),
                "credential_storage": "legacy-local" if "user" in data else "ssh-only",
            }
        )
    )
