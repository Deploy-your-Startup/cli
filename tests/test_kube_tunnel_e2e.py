"""GIVEN real SSH and mutual TLS, WHEN native clients run, THEN access recovers."""

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def run(*args, check=False, **kwargs):
    return subprocess.run(
        list(args), capture_output=True, text=True, timeout=90, check=check, **kwargs
    )


@pytest.fixture(scope="module")
def ssh_server(tmp_path_factory):
    if not shutil.which("docker") or run("docker", "info").returncode:
        pytest.skip("requires Docker for a real SSH server")
    if not shutil.which("kubectl"):
        pytest.skip("requires kubectl for the native client E2E")
    root = tmp_path_factory.mktemp("ssh-tunnel")
    image = f"startup-kube-tunnel-test:{uuid.uuid4().hex[:12]}"
    run(
        "ssh-keygen",
        "-q",
        "-t",
        "ed25519",
        "-N",
        "",
        "-f",
        str(root / "id"),
        check=True,
    )
    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(root / "ca.key"),
        "-out",
        str(root / "ca.crt"),
        "-subj",
        "/CN=test-ca",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-days",
        "1",
        check=True,
    )
    for name in ("server", "client"):
        run(
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(root / f"{name}.key"),
            "-out",
            str(root / f"{name}.csr"),
            "-subj",
            f"/CN={name}",
            check=True,
        )
        ext = root / f"{name}.ext"
        ext.write_text(
            "subjectAltName=DNS:cluster.test,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
            if name == "server"
            else "extendedKeyUsage=clientAuth\n"
        )
        run(
            "openssl",
            "x509",
            "-req",
            "-in",
            str(root / f"{name}.csr"),
            "-CA",
            str(root / "ca.crt"),
            "-CAkey",
            str(root / "ca.key"),
            "-CAcreateserial",
            "-out",
            str(root / f"{name}.crt"),
            "-days",
            "1",
            "-extfile",
            str(ext),
            check=True,
        )
    config = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": "default",
        "clusters": [
            {
                "name": "default",
                "cluster": {
                    "server": "https://127.0.0.1:6443",
                    "certificate-authority-data": base64.b64encode(
                        (root / "ca.crt").read_bytes()
                    ).decode(),
                },
            }
        ],
        "users": [
            {
                "name": "default",
                "user": {
                    "client-certificate-data": base64.b64encode(
                        (root / "client.crt").read_bytes()
                    ).decode(),
                    "client-key-data": base64.b64encode(
                        (root / "client.key").read_bytes()
                    ).decode(),
                },
            }
        ],
        "contexts": [
            {"name": "default", "context": {"cluster": "default", "user": "default"}}
        ],
    }
    (root / "k3s.yaml").write_text(yaml.safe_dump(config))
    shutil.copy(ROOT / "tests/fixtures/kube-tunnel/server.py", root / "server.py")
    (root / "Dockerfile").write_text("""FROM alpine:3.20
RUN apk add --no-cache openssh-server python3 && ssh-keygen -A && passwd -d root
RUN mkdir -p /root/.ssh /etc/rancher/k3s && chmod 700 /root/.ssh
COPY id.pub /root/.ssh/authorized_keys
RUN chmod 600 /root/.ssh/authorized_keys
COPY . /fixtures/
RUN cp /fixtures/k3s.yaml /etc/rancher/k3s/k3s.yaml
CMD ["sh", "-c", "python3 /fixtures/server.py & exec /usr/sbin/sshd -D -e -o AllowTcpForwarding=yes"]
""")
    run("docker", "build", "-q", "-t", image, str(root), check=True)
    container = run(
        "docker",
        "run",
        "--rm",
        "-d",
        "-p",
        "127.0.0.1::22",
        image,
        check=True,
    ).stdout.strip()
    try:
        port = (
            run("docker", "port", container, "22/tcp", check=True)
            .stdout.strip()
            .split(":")[-1]
        )
        for _ in range(30):
            scan = run("ssh-keyscan", "-p", port, "127.0.0.1")
            if scan.stdout:
                break
            time.sleep(0.2)
        (root / "known_hosts").write_text(scan.stdout)
        yield root, port
    finally:
        run("docker", "rm", "-f", container)
        run("docker", "image", "rm", image)


@pytest.fixture
def client(tmp_path, ssh_server):
    root, port = ssh_server
    # OpenSSH uses the passwd home, not HOME. Supply isolated host trust while
    # still executing the real system ssh for every connection and control call.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    wrapper = bindir / "ssh"
    wrapper.write_text(
        f'#!/bin/sh\nexec {shutil.which("ssh")} -o UserKnownHostsFile="{root / "known_hosts"}" "$@"\n'
    )
    wrapper.chmod(0o755)
    scp = bindir / "scp"
    scp.write_text(
        f'#!/bin/sh\nexec {shutil.which("scp")} -o UserKnownHostsFile="{root / "known_hosts"}" "$@"\n'
    )
    scp.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT / "src"),
    }
    env.pop("KUBECONFIG", None)
    config = tmp_path / ".kube/config"
    config.parent.mkdir()
    # A short path also exercises macOS's small Unix socket path limit.
    state_root = Path(tempfile.mkdtemp(prefix="kt-", dir="/tmp"))
    (config.parent / "startup").symlink_to(state_root)
    args = [
        sys.executable,
        "-m",
        "cli.startup",
        "cluster",
        "connect",
        "--context",
        "shop",
        "--host",
        "127.0.0.1",
        "--ssh-port",
        port,
        "--identity-file",
        str(root / "id"),
        "--tls-server-name",
        "cluster.test",
        "--namespace",
        "shop",
    ]
    try:
        yield env, config, args
    finally:
        for state in state_root.glob("*.yml"):
            run(
                str(wrapper),
                "-S",
                str(state.with_suffix(".sock")),
                "-O",
                "exit",
                "root@127.0.0.1",
                env=env,
            )
        shutil.rmtree(state_root)


def test_native_clients_reconnect_and_keep_namespaces(client):
    env, config, args = client
    # GIVEN SSH-only Kubernetes and two startup namespaces.
    result = run(*args, env=env)
    assert result.returncode == 0, result.stderr
    second = args.copy()
    second[second.index("--context") + 1] = "other"
    second[second.index("--namespace") + 1] = "other"
    assert run(*second, env=env).returncode == 0
    before = yaml.safe_load(config.read_text())
    # WHEN the tunnel disappears (also the state after a reboot).
    for state in (config.parent / "startup").glob("*.yml"):
        run(
            "ssh",
            "-S",
            str(state.resolve().with_suffix(".sock")),
            "-O",
            "exit",
            "root@127.0.0.1",
            env=env,
            check=True,
        )

    # THEN concurrent real kubectl clients restart it without a connect command.
    def pods(context):
        return run(
            "kubectl",
            "--kubeconfig",
            str(config),
            "--context",
            context,
            "--request-timeout=15s",
            "get",
            "pods",
            "-o",
            "json",
            env=env,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(pods, ["shop", "shop", "other", "other"]))
    for context, result in zip(
        ["shop", "shop", "other", "other"], results, strict=True
    ):
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["items"][0]["metadata"]["namespace"] == context
    assert yaml.safe_load(config.read_text()) == before
    assert config.stat().st_mode & 0o777 == 0o600
    for state in (config.parent / "startup").glob("*.yml"):
        assert state.stat().st_mode & 0o777 == 0o600
    # WHEN setup is repeated THEN it preserves the existing port and other context.
    assert run(*args, env=env).returncode == 0
    assert yaml.safe_load(config.read_text()) == before


def test_denied_ssh_and_wrong_tls_leave_config_unchanged(client, tmp_path):
    env, config, args = client
    # GIVEN an existing unrelated context and an unauthorized SSH key.
    original = "apiVersion: v1\nkind: Config\ncontexts: []\nclusters: []\nusers: []\n"
    config.write_text(original)
    key = tmp_path / "denied"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), check=True)
    denied = args.copy()
    denied[denied.index("--identity-file") + 1] = str(key)
    # WHEN connecting without authorization THEN configuration is preserved.
    result = run(*denied, env=env)
    assert result.returncode != 0
    assert "Cannot fetch kubeconfig" in result.stderr
    assert config.read_text() == original
    # WHEN the server identity is wrong THEN TLS is never silently disabled.
    bad_tls = args.copy()
    bad_tls[bad_tls.index("--tls-server-name") + 1] = "wrong.test"
    result = run(*bad_tls, env=env)
    assert result.returncode != 0
    assert "Cannot verify Kubernetes TLS" in result.stderr
    assert config.read_text() == original


def test_reject_option_injection_without_writing_config(client):
    env, config, args = client
    # GIVEN an invalid SSH address, WHEN connect runs, THEN no SSH/config mutation.
    args[args.index("--host") + 1] = "-oProxyCommand=bad"
    result = run(*args, env=env)
    assert result.returncode != 0
    assert "Invalid SSH host" in result.stderr
    assert not config.exists()


def test_occupied_port_fails_without_replacing_configuration(client):
    env, config, args = client
    # GIVEN a configured tunnel whose port is occupied after it stops.
    assert run(*args, env=env).returncode == 0
    original = config.read_bytes()
    state = next((config.parent / "startup").glob("*.yml")).resolve()
    settings = yaml.safe_load(state.read_text())
    run(
        "ssh",
        "-S",
        str(state.with_suffix(".sock")),
        "-O",
        "exit",
        "root@127.0.0.1",
        env=env,
        check=True,
    )
    with socket.socket() as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", settings["local_port"]))
        occupied.listen()
        # WHEN a native client connects THEN fail clearly rather than adopting
        # an unrelated listener or silently disabling certificate verification.
        result = run(
            "kubectl",
            "--kubeconfig",
            str(config),
            "--context",
            "shop",
            "--request-timeout=5s",
            "get",
            "--raw=/readyz",
            env=env,
        )
    assert result.returncode != 0
    assert "Cannot start Kubernetes SSH tunnel" in result.stderr
    assert config.read_bytes() == original


def test_ansible_kubeconfig_defaults_to_ssh_and_preserves_other_contexts(
    client, ssh_server, tmp_path
):
    env, config, _ = client
    root, port = ssh_server
    roles = Path(
        os.environ.get("STARTUP_TEST_DEPLOY_TEMPLATE", ROOT.parent / "deploy-template")
    )
    source = (
        roles.as_uri()
        if roles.is_dir()
        else "https://github.com/Deploy-your-Startup/deploy-template.git"
    )
    # GIVEN a real project inventory, real encrypted token and the real shared
    # checkout. Static inventory replaces cloud discovery, not CLI behavior.
    project = tmp_path / "deployment"
    group = project / "group_vars"
    group.mkdir(parents=True)
    (group / "all.yml").write_text("k8s_namespace: shop\n")
    (project / "inventory.hcloud.yml").write_text(
        yaml.safe_dump(
            {
                "all": {
                    "children": {
                        "masters": {
                            "hosts": {"shop-master-0": {"ansible_host": "127.0.0.1"}}
                        }
                    }
                }
            }
        )
    )
    password = tmp_path / "password"
    password.write_text("synthetic-password")
    token = run(
        str(Path(sys.executable).parent / "ansible-vault"),
        "encrypt",
        "--vault-password-file",
        str(password),
        "--output",
        "-",
        input="synthetic-token",
        check=True,
    )
    (project / "hcloud_token_production").write_text(token.stdout)
    password.unlink()
    env = {
        **env,
        "STARTUP_VAULT_PASSWORD": "synthetic-password",
        "UV_PROJECT_ENVIRONMENT": str(Path(sys.executable).parent.parent),
        "UV_NO_SYNC": "1",
        "ANSIBLE_COLLECTIONS_PATH": str(Path.home() / ".ansible/collections"),
    }
    unrelated = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": "unrelated",
        "contexts": [
            {
                "name": "unrelated",
                "context": {"cluster": "old", "user": "old", "namespace": "team"},
            }
        ],
        "clusters": [{"name": "old", "cluster": {"server": "https://example.invalid"}}],
        "users": [{"name": "old", "user": {"token": "synthetic"}}],
    }
    config.write_text(yaml.safe_dump(unrelated))
    # WHEN the public kubeconfig command runs with its defaults.
    result = run(
        sys.executable,
        "-m",
        "cli.startup",
        "ansible",
        "kubeconfig",
        "--working-directory",
        str(project),
        "--environment",
        "production",
        "--repo-url",
        source,
        "--version",
        run("git", "-C", str(roles), "rev-parse", "HEAD", check=True).stdout.strip(),
        "--context-name",
        "shop",
        "--ssh-port",
        port,
        "--ssh-identity",
        str(root / "id"),
        "--no-make-current",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # THEN namespace, automatic access and the unrelated selection all survive.
    saved = yaml.safe_load(config.read_text())
    assert saved["current-context"] == "unrelated"
    assert unrelated["contexts"][0] in saved["contexts"]
    result = run(
        "kubectl",
        "--kubeconfig",
        str(config),
        "--context",
        "shop",
        "--request-timeout=15s",
        "get",
        "pods",
        "-o",
        "json",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["items"][0]["metadata"]["namespace"] == "shop"
