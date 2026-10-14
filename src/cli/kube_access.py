"""On-demand SSH access for native Kubernetes clients on macOS and Linux."""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import yaml


def private_write(path: Path, value: dict) -> None:
    """Replace a credential-bearing file atomically, with owner-only access."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(yaml.safe_dump(value, sort_keys=False))
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def access_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def ssh_command(settings: dict) -> list[str]:
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        "-p",
        str(settings["ssh_port"]),
    ]
    if settings.get("identity"):
        args += ["-i", settings["identity"], "-o", "IdentitiesOnly=yes"]
    return args


def ssh_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, text=True, capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(
            "SSH unavailable or timed out. Check your network and SSH agent."
        ) from exc


def ensure_tunnel(settings: dict, state: Path) -> None:
    """Serialize connection setup across kubectl, k9s and other clients."""
    control = str(state.with_suffix(".sock"))
    if len(control.encode()) > 95:
        raise click.ClickException(
            "Tunnel state path is too long for an SSH control socket."
        )
    destination = f"{settings['ssh_user']}@{settings['host']}"
    with access_lock(state.with_suffix(".lock")):
        check = ssh_run(
            [*ssh_command(settings), "-S", control, "-O", "check", destination]
        )
        if check.returncode != 0:
            Path(control).unlink(missing_ok=True)
            result = ssh_run(
                [
                    *ssh_command(settings),
                    "-M",
                    "-S",
                    control,
                    "-f",
                    "-N",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-o",
                    "ServerAliveInterval=10",
                    "-o",
                    "ServerAliveCountMax=2",
                    "-o",
                    "ControlPersist=120",
                    "-L",
                    f"127.0.0.1:{settings['local_port']}:127.0.0.1:6443",
                    destination,
                ]
            )
            if result.returncode != 0:
                raise click.ClickException(
                    "Cannot start Kubernetes SSH tunnel. Check SSH key/agent, known_hosts "
                    "and local port availability. " + result.stderr.strip()
                )


def verify_tunnel(settings: dict) -> None:
    """Check the real server certificate through the forward before saving config."""
    ca = base64.b64decode(settings["cluster"]["certificate-authority-data"]).decode()
    context = ssl.create_default_context(cadata=ca)
    try:
        with (
            socket.create_connection(
                ("127.0.0.1", settings["local_port"]), timeout=10
            ) as raw,
            context.wrap_socket(raw, server_hostname=settings["tls_name"]),
        ):
            pass
    except (OSError, ssl.SSLError) as exc:
        raise click.ClickException(
            f"Cannot verify Kubernetes TLS through SSH; kubeconfig was not changed: {exc}"
        ) from exc


def configure_access(
    kubeconfig: dict,
    context_name: str,
    host: str,
    ssh_user: str = "root",
    ssh_port: int = 22,
    identity: str | None = None,
    tls_name: str | None = None,
) -> dict:
    """Prepare a context without changing any other context sharing its cluster."""
    for value in (host, ssh_user, tls_name or host):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or value.startswith("-"):
            raise click.ClickException("Invalid SSH host, user or TLS name.")
    result = copy.deepcopy(kubeconfig)
    selected = next((c for c in result["contexts"] if c["name"] == context_name), None)
    if selected is None:
        raise click.ClickException(f"Context not found: {context_name}")
    cluster = next(
        c["cluster"]
        for c in result["clusters"]
        if c["name"] == selected["context"]["cluster"]
    )
    user = next(
        c["user"] for c in result["users"] if c["name"] == selected["context"]["user"]
    )
    if not all(k in user for k in ("client-certificate-data", "client-key-data")):
        raise click.ClickException(
            "SSH access requires a freshly fetched certificate-based kubeconfig."
        )
    if "certificate-authority-data" not in cluster:
        raise click.ClickException(
            "SSH access requires an embedded Kubernetes CA certificate."
        )
    root = (Path.home() / ".kube" / "startup").resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    key = hashlib.sha256(context_name.encode()).hexdigest()[:16]
    state = root / f"{key}.yml"
    with access_lock(root / "configure.lock"):
        previous = yaml.safe_load(state.read_text()) if state.exists() else None
        connection = {
            "host": host,
            "ssh_user": ssh_user,
            "ssh_port": ssh_port,
            "identity": identity,
        }
        if previous and any(previous[k] != v for k, v in connection.items()):
            raise click.ClickException(
                "SSH destination changed. Remove the old tunnel configuration before reconnecting."
            )
        if previous:
            port = previous["local_port"]
        else:
            reserved = {
                yaml.safe_load(p.read_text())["local_port"] for p in root.glob("*.yml")
            }
            for _ in range(100):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                if port not in reserved:
                    break
            else:
                raise click.ClickException("No free local Kubernetes tunnel port.")
        settings = {
            **connection,
            "local_port": port,
            "tls_name": tls_name or host,
            "cluster": cluster,
            "user": user,
            "context": context_name,
        }
        ensure_tunnel(settings, state)
        try:
            verify_tunnel(settings)
        except click.ClickException:
            if previous is None:
                ssh_run(
                    [
                        *ssh_command(settings),
                        "-S",
                        str(state.with_suffix(".sock")),
                        "-O",
                        "exit",
                        f"{ssh_user}@{host}",
                    ]
                )
            raise
        private_write(state, settings)
    managed_name = f"startup-ssh-{key}"
    selected["context"].update(cluster=managed_name, user=managed_name)
    result["clusters"] = [c for c in result["clusters"] if c["name"] != managed_name]
    result["users"] = [u for u in result["users"] if u["name"] != managed_name]
    result["clusters"].append(
        {
            "name": managed_name,
            "cluster": {
                "server": f"https://127.0.0.1:{port}",
                "certificate-authority-data": cluster["certificate-authority-data"],
                "tls-server-name": tls_name or host,
            },
        }
    )
    result["users"].append(
        {
            "name": managed_name,
            "user": {
                "exec": {
                    "apiVersion": "client.authentication.k8s.io/v1",
                    "interactiveMode": "Never",
                    "command": sys.executable,
                    "args": ["-I", "-m", "cli.kube_access", str(state)],
                    "installHint": "Reinstall the startup CLI and run startup cluster connect again.",
                }
            },
        }
    )
    return result


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
    fetched = ssh_run(
        [
            *ssh_command(connection),
            f"{ssh_user}@{host}",
            "cat /etc/rancher/k3s/k3s.yaml",
        ]
    )
    if fetched.returncode:
        raise click.ClickException(
            "Cannot fetch kubeconfig over SSH. Check SSH access and permissions. "
            + fetched.stderr.strip()
        )
    try:
        source = yaml.safe_load(fetched.stdout)
        source["contexts"][0]["name"] = context_name
        source["current-context"] = context_name
        # k3s exports one context. Use context-specific names when merging.
        source["clusters"][0]["name"] = context_name
        source["users"][0]["name"] = context_name
        source["contexts"][0]["context"].update(cluster=context_name, user=context_name)
    except (KeyError, IndexError, TypeError, yaml.YAMLError) as exc:
        raise click.ClickException("SSH returned an invalid k3s kubeconfig.") from exc
    with access_lock(config_path.with_name(config_path.name + ".startup.lock")):
        existing: dict = (
            yaml.safe_load(config_path.read_text())
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


def credential(state: Path) -> None:
    """ExecCredential stdout is consumed only by the Kubernetes client."""
    if not os.environ.get("KUBERNETES_EXEC_INFO"):
        raise click.ClickException("This helper is only for Kubernetes clients.")
    try:
        settings = yaml.safe_load(state.read_text())
        ensure_tunnel(settings, state)
        user = settings["user"]
        status = {
            "clientCertificateData": base64.b64decode(
                user["client-certificate-data"]
            ).decode(),
            "clientKeyData": base64.b64decode(user["client-key-data"]).decode(),
            "expirationTimestamp": (datetime.now(UTC) + timedelta(seconds=20))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        print(
            json.dumps(
                {
                    "apiVersion": "client.authentication.k8s.io/v1",
                    "kind": "ExecCredential",
                    "status": status,
                }
            )
        )
    except (OSError, KeyError, ValueError, yaml.YAMLError) as exc:
        raise click.ClickException(
            "Cannot read tunnel credentials. Run startup cluster connect again."
        ) from exc


if __name__ == "__main__":
    try:
        credential(Path(sys.argv[1]))
    except click.ClickException as error:
        error.show()
        sys.exit(1)
