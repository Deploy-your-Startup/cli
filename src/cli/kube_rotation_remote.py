"""Single-node K3s client-CA recovery; executed over SSH by the public CLI.

Only the client CA is replaced. Server, etcd, request-header and service-account
trust remain unchanged. Subprocess output may contain secrets and stays private.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path


def run(*args: str) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f"Remote operation failed: {args[0]}")
    return result.stdout


def check_node() -> None:
    nodes = json.loads(run("k3s", "kubectl", "get", "nodes", "-o", "json"))["items"]
    if len(nodes) != 1 or not Path("/var/lib/rancher/k3s/server/tls").is_dir():
        raise RuntimeError(
            "Client-CA recovery supports one default-data-dir server only."
        )


def rotate(expected_ca: str) -> dict:
    if os.geteuid() != 0:
        raise RuntimeError("Client-CA recovery requires root over SSH.")
    check_node()
    server = Path("/var/lib/rancher/k3s/server")
    actual_ca = hashlib.sha256(
        run(
            "openssl",
            "x509",
            "-in",
            str(server / "tls/client-ca.crt"),
            "-outform",
            "DER",
        )
    ).hexdigest()
    if actual_ca != expected_ca:
        raise RuntimeError("Client CA changed; re-inspect the selected connection.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    recovery = server / f"client-ca-recovery-{stamp}"
    recovery.mkdir(mode=0o700)
    run("k3s", "etcd-snapshot", "save", "--name", f"client-ca-recovery-{stamp}")
    with tarfile.open(recovery / "certificates.tar.gz", "w:gz") as archive:
        archive.add(server / "tls", arcname="server/tls")
        for path in (server.parent / "agent").glob("*.crt"):
            archive.add(path, arcname="agent/" + path.name)
        for path in (server.parent / "agent").glob("*.key"):
            archive.add(path, arcname="agent/" + path.name)
        archive.add("/etc/rancher/k3s/k3s.yaml", arcname="k3s.yaml")
    (recovery / "certificates.tar.gz").chmod(0o600)
    stage = recovery / "staged"
    (stage / "tls").mkdir(parents=True, mode=0o700)
    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(stage / "tls/client-ca.key"),
        "-out",
        str(stage / "tls/client-ca.crt"),
        "-days",
        "3650",
        "-subj",
        f"/CN=startup-client-ca@{stamp}",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    (stage / "tls/client-ca.key").chmod(0o600)
    run("k3s", "certificate", "rotate-ca", "--path", str(stage), "--force")
    # Renew all leaves issued by the client CA, including the local agent.
    # Some services also renew internal serving leaves under the unchanged server CA.
    services = (
        "admin",
        "api-server",
        "controller-manager",
        "scheduler",
        "supervisor",
        "k3s-controller",
        "cloud-controller",
        "kubelet",
        "kube-proxy",
    )
    run("systemctl", "stop", "k3s")
    try:
        flags = [value for service in services for value in ("--service", service)]
        run("k3s", "certificate", "rotate", *flags)
    finally:
        run("systemctl", "start", "k3s")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        ready = subprocess.run(
            ["k3s", "kubectl", "get", "--raw=/readyz", "--request-timeout=5s"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if ready.returncode == 0 and ready.stdout.strip() == b"ok":
            return {"recovery_directory": str(recovery), "api_ready": True}
        time.sleep(2)
    raise RuntimeError("K3s did not become ready. Recovery files remain on the server.")


if __name__ == "__main__":
    try:
        print(json.dumps(rotate(sys.argv[1])))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Never print a process result, traceback, argv or credential-bearing input.
        print(
            "Client-CA rotation failed. Inspect the server through SSH; recovery files were retained.",
            file=sys.stderr,
        )
        sys.exit(1)
