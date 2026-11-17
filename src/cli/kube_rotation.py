"""Explicit recovery of an exposed administrator client certificate."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import ssl
import subprocess
import tempfile
from pathlib import Path

import click

from cli import kube_rotation_remote
from cli.kube_access import ensure_tunnel, private_write, ssh_command, verify_tunnel
from cli.kube_credentials import fetch_kubeconfig, public_state, read_state
from cli.kube_inspect import connection_state


def ca_fingerprint(user: dict) -> str:
    chain = base64.b64decode(user["client-certificate-data"]).decode()
    certificates = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", chain, re.S
    )
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificates[-1])).hexdigest()


def authenticated_status(settings: dict, user: dict) -> int:
    """Verify protected API access, retaining private material only in a temp dir."""
    with tempfile.TemporaryDirectory(prefix="startup-client-check-") as directory:
        cert, key = (Path(directory) / name for name in ("cert.pem", "key.pem"))
        for path, field in (
            (cert, "client-certificate-data"),
            (key, "client-key-data"),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(base64.b64decode(user[field]))
        ca = base64.b64decode(
            settings["cluster"]["certificate-authority-data"]
        ).decode()
        tls = ssl.create_default_context(cadata=ca)
        tls.load_cert_chain(cert, key)
        # The SSH destination's public address is in the server certificate;
        # verify_tunnel separately checks that name before every rotation.
        connection = http.client.HTTPSConnection(
            "127.0.0.1", settings["local_port"], context=tls, timeout=10
        )
        try:
            connection.request("GET", "/api/v1/namespaces/default/pods?limit=1")
            response = connection.getresponse()
            response.read()
            return response.status
        except ssl.SSLError:
            return 401
        finally:
            connection.close()


def verify_recovery(settings: dict, current: dict, previous: list[dict]) -> None:
    if authenticated_status(settings, current) != 200:
        raise click.ClickException("New administrator access could not be verified.")
    for user in previous:
        if current["client-key-data"] == user["client-key-data"]:
            raise click.ClickException("Administrator private key was not replaced.")
        if authenticated_status(settings, user) not in (401, 403):
            raise click.ClickException(
                "Previous administrator access was not rejected."
            )


@click.command("rotate-client-ca")
@click.option("--context", "context_name", required=True)
@click.option("--kubeconfig", "config_path", type=click.Path(path_type=Path))
@click.option(
    "--execute",
    is_flag=True,
    help="Replace client trust on the selected single-node cluster.",
)
@click.option(
    "--backup-confirmed",
    is_flag=True,
    help="Confirm application backups were verified.",
)
def rotate_client_ca(
    context_name: str, config_path: Path | None, execute: bool, backup_confirmed: bool
) -> None:
    """Invalidate old K3s client certificates; previews by default.

    Requires a single-node, embedded-etcd cluster with the default data directory.
    Existing client certificates stop working. Refresh other saved admin copies.
    """
    config_path = config_path or Path.home() / ".kube/config"
    state, _ = connection_state(config_path, context_name)
    stored = read_state(state)
    settings = public_state(stored)
    click.echo(
        f"Client-CA recovery: {context_name}. All existing client certificates in this cluster will be invalidated; K3s will restart."
    )
    if not execute:
        click.echo(
            "Preview only. Execute with --execute --backup-confirmed after verifying application backups."
        )
        return
    if not backup_confirmed:
        raise click.ClickException(
            "Verify application backups and pass --backup-confirmed."
        )
    ensure_tunnel(settings, state)
    verify_tunnel(settings)
    original = fetch_kubeconfig(settings, state)["users"][0]["user"]
    previous = [original]
    legacy = stored.get("user", {})
    if isinstance(legacy, dict) and all(
        isinstance(legacy.get(field), str)
        for field in ("client-certificate-data", "client-key-data")
    ):
        previous.append(legacy)
    if authenticated_status(settings, original) != 200:
        raise click.ClickException(
            "The current administrator credential could not be verified."
        )
    click.echo("Saving cluster recovery data and replacing client trust over SSH…")
    try:
        result = subprocess.run(
            [
                *ssh_command(settings),
                f"{settings['ssh_user']}@{settings['host']}",
                "python3",
                "-",
                ca_fingerprint(original),
            ],
            input=Path(kube_rotation_remote.__file__).read_text(),
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise click.ClickException(
            "Rotation transport failed. Check K3s over SSH before retrying."
        ) from None
    if result.returncode:
        raise click.ClickException(
            "Rotation failed. Check K3s over SSH; no remote process output was logged."
        )
    receipt = json.loads(result.stdout)
    current = fetch_kubeconfig(settings, state)["users"][0]["user"]
    verify_recovery(settings, current, previous)
    private_write(state, settings)
    click.echo(
        "Verified: new administrator access works; the previous certificate is rejected."
    )
    click.echo(f"Protected recovery data: {receipt['recovery_directory']}")
