"""Keep Kubernetes credentials in memory and out of connection metadata."""

from __future__ import annotations

from pathlib import Path

import click
import yaml


def read_yaml(text: str) -> dict:
    """Never include parser excerpts from credential-bearing input in errors."""
    try:
        value = yaml.safe_load(text)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, yaml.YAMLError):
        raise click.ClickException("Invalid Kubernetes connection data.") from None


def read_state(path: Path) -> dict:
    try:
        return read_yaml(path.read_text())
    except OSError:
        raise click.ClickException(
            "Cannot read Kubernetes connection metadata."
        ) from None


def public_state(settings: dict) -> dict:
    """An allowlist prevents legacy credentials or future fields being saved."""
    try:
        connection = {
            key: settings[key]
            for key in (
                "host",
                "ssh_user",
                "ssh_port",
                "identity",
                "local_port",
                "tls_name",
                "context",
            )
        }
        if any(isinstance(value, (dict, list)) for value in connection.values()):
            raise ValueError
        connection["cluster"] = {
            "certificate-authority-data": settings["cluster"][
                "certificate-authority-data"
            ]
        }
        return connection
    except (KeyError, TypeError, ValueError):
        raise click.ClickException("Invalid Kubernetes connection metadata.") from None


def fetch_kubeconfig(settings: dict, state: Path | None = None) -> dict:
    """SSH stdout is private input, never diagnostic output."""
    from cli.kube_access import ssh_command, ssh_run

    control = ["-S", str(state.with_suffix(".sock"))] if state else []
    response = ssh_run(
        [
            *ssh_command(settings),
            *control,
            f"{settings['ssh_user']}@{settings['host']}",
            "cat /etc/rancher/k3s/k3s.yaml",
        ]
    )
    if response.returncode:
        raise click.ClickException(
            "Cannot fetch kubeconfig over SSH. Check SSH access and permissions."
        )
    try:
        value = read_yaml(response.stdout)
        user = value["users"][0]["user"]
        for key in ("client-certificate-data", "client-key-data"):
            if not isinstance(user[key], str):
                raise ValueError
        return value
    except (click.ClickException, KeyError, IndexError, TypeError, ValueError):
        raise click.ClickException("SSH returned an invalid k3s kubeconfig.") from None
