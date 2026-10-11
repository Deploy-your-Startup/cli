"""GIVEN an attached startup, WHEN it backs up or restores, THEN only its namespace.

The real public CLI renders nothing by hand: a real Copier project is attached to
an owner cluster descriptor, the CLI writes the static inventory, decrypts the
vaulted deploy key and runs the published backup/restore playbooks over real SSH.
The owner's master node is a local SSH container; only Kubernetes is replaced by
a `kubectl` stand-in that keeps each namespace's data in its own directory.
"""

import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from cli.bootstrap import TEMPLATE_VAULT_PASSWORD, template_replacements
from cli.cluster_commands import configure_project_cluster
from cli.template_commands import DEFAULT_TEMPLATE_VERSION, render_project
from cli.wizard.context import BootstrapContext

ROOT = Path(__file__).resolve().parents[1]
# Published releases only, so a developer's draft checkout cannot leak in.
LOCAL_TEMPLATE = ROOT.parent / "django-backend-template"
TEMPLATE = (
    str(LOCAL_TEMPLATE)
    if (LOCAL_TEMPLATE / ".git").exists()
    else "https://github.com/Deploy-your-Startup/django-backend-template.git"
)
LOCAL_ROLES = ROOT.parent / "deploy-template"
# A file:// URI is cloned at ROLES_VERSION; a plain path would copy the worktree.
ROLES = (
    LOCAL_ROLES.as_uri()
    if (LOCAL_ROLES / ".git").exists()
    else "https://github.com/Deploy-your-Startup/deploy-template.git"
)
# The published deployment roles release that ships shared-cluster support.
ROLES_VERSION = "v0.1.4"
OWNER = "cluster-owner"
ATTACHED = "second-startup"
IMAGE = "startup-cli-test-cluster-master:1"

# Kubernetes stand-in on the owner's master. Every namespace is a directory; a
# command sent with `kubectl exec` runs with /app mapped into that namespace's
# directory, so the playbook's real shell (tar, find, gzip) does the work.
KUBECTL = r"""#!/bin/bash
set -euo pipefail
namespace=""
arguments=()
while [ $# -gt 0 ]; do
  case "$1" in
    -n) namespace="$2"; shift 2 ;;
    -i) shift ;;
    --) shift; break ;;
    *) arguments+=("$1"); shift ;;
  esac
done
echo "namespace=$namespace ${arguments[0]} ${arguments[1]}" >> /srv/k8s/calls.log
root="/srv/k8s/$namespace"
if [ -z "$namespace" ] || [ ! -d "$root" ]; then
  echo "Error from server (NotFound): namespaces \"$namespace\" not found" >&2
  exit 1
fi
case "${arguments[0]}" in
  get)
    field="$(printf '%s' "${arguments[*]}" | sed -n 's/.*{\.data\.\([a-z_]*\)}.*/\1/p')"
    printf '%s' "$(cat "$root/secret/$field")" | base64
    ;;
  exec)
    command=()
    for argument in "$@"; do command+=("${argument//\/app/$root/app}"); done
    cd "$root"
    NAMESPACE_ROOT="$root" POSTGRES_USER=postgres PATH="/srv/k8s/bin:$PATH" \
      exec "${command[@]}"
    ;;
  *) echo "unsupported kubectl call: ${arguments[*]}" >&2; exit 1 ;;
esac
"""
PG_DUMP = r"""#!/bin/bash
set -euo pipefail
database="$(printf '%s\n' "$@" | sed -n '/^-d$/{n;p;}')"
[ "$database" = "$(cat "$NAMESPACE_ROOT/secret/backend_db_name")" ]
cat "$NAMESPACE_ROOT/db.sql"
"""
PSQL = r"""#!/bin/bash
set -euo pipefail
if printf '%s\n' "$@" | grep -qx -- '-tAc'; then echo t; exit 0; fi
cat >> "$NAMESPACE_ROOT/psql.log"
"""
UV = r"""#!/bin/bash
echo "[{\"model\": \"app.namespace\", \"fields\": {\"root\": \"$NAMESPACE_ROOT\"}}]"
"""
DOCKERFILE = """FROM alpine:3.20
RUN apk add --no-cache openssh-server bash python3 coreutils findutils tar gzip \
 && ssh-keygen -A \
 && mkdir -p /root/.ssh /srv/k8s/bin \
 && chmod 700 /root/.ssh \
 && sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
 && passwd -u root || true
COPY kubectl /usr/local/bin/kubectl
COPY pg_dump psql uv /srv/k8s/bin/
RUN chmod 755 /usr/local/bin/kubectl /srv/k8s/bin/*
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""


def docker(*arguments, **kwargs):
    return subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
        **kwargs,
    )


def write_namespace(container, namespace, database, rows):
    script = (
        f"set -e; r=/srv/k8s/{namespace}; mkdir -p $r/secret $r/app/media; "
        f"printf %s {database} > $r/secret/backend_db_name; "
        f"printf %s {namespace}_user > $r/secret/backend_db_user; "
        f"printf '%s\\n' '{rows}' > $r/db.sql; "
        f"printf %s {namespace} > $r/app/media/owner.txt"
    )
    docker("exec", container, "sh", "-c", script)


@pytest.fixture(scope="module")
def master(tmp_path_factory):
    if shutil.which("docker") is None or (
        subprocess.run(["docker", "info"], capture_output=True, check=False).returncode
        != 0
    ):
        pytest.skip("needs a running Docker daemon for the SSH master node")
    context = tmp_path_factory.mktemp("master-image")
    for name, content in {
        "Dockerfile": DOCKERFILE,
        "kubectl": KUBECTL,
        "pg_dump": PG_DUMP,
        "psql": PSQL,
        "uv": UV,
    }.items():
        (context / name).write_text(content)
    docker("build", "-q", "-t", IMAGE, str(context))
    container = docker("run", "-d", "--rm", "-p", "127.0.0.1::22", IMAGE).stdout.strip()
    try:
        port = int(docker("port", container, "22/tcp").stdout.split(":")[-1])
        yield container, port
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, check=False
        )


def encrypt(tmp_path, content):
    password = tmp_path / "vault-password"
    password.write_text(TEMPLATE_VAULT_PASSWORD)
    try:
        return subprocess.run(
            [
                str(Path(sys.executable).parent / "ansible-vault"),
                "encrypt",
                "--vault-password-file",
                str(password),
                "--output",
                "-",
            ],
            input=content,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    finally:
        password.unlink()


@pytest.fixture
def attached(tmp_path, master):
    container, port = master
    docker("exec", container, "sh", "-c", "rm -rf /srv/k8s/*-* /srv/k8s/calls.log")
    write_namespace(container, OWNER, "owner_db", "OWNER ROWS")
    write_namespace(container, ATTACHED, "second_db", "SECOND ROWS")
    # GIVEN a real application template attached to the owner's cluster, whose
    # master node is the SSH container and whose worker is never reachable.
    descriptor = {
        "schema": 1,
        "id": "a" * 32,
        "owner": f"example-owner/{OWNER}",
        "environment": "production",
        "nodes": [
            {
                "name": f"{OWNER}-master-0",
                "host": "127.0.0.1",
                "ssh_user": "root",
                "role": "master",
            },
            {
                "name": f"{OWNER}-worker-0",
                "host": "203.0.113.11",
                "ssh_user": "root",
                "role": "worker",
            },
        ],
    }
    ctx = BootstrapContext(
        project_name=ATTACHED,
        base_domain=f"{ATTACHED}.example.com",
        additional_domains="",
        github_username="example-owner",
        postgres_version="18.6",
        sentry_dsn="",
        output_dir=tmp_path,
        shared_cluster=False,
        provider="byos",
        cluster_config=descriptor,
    )
    render_project(
        ctx.project_dir,
        template_replacements(
            project_name=ATTACHED,
            base_domain=ctx.base_domain,
            additional_domains="",
            github_username="example-owner",
            docker_registry_host="ghcr.io",
            postgres_version="18.6",
            k8s_namespace=ATTACHED,
            ci_public_key="ssh-ed25519 AAAATEST integration",
            user_public_key="ssh-ed25519 AAAATEST operator",
        ),
        source=TEMPLATE,
        version=DEFAULT_TEMPLATE_VERSION,
    )
    configure_project_cluster(ctx)
    # The project's deploy key is the only credential; the owner authorizes it.
    key = tmp_path / "deploy-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    (ctx.deployment_dir / "ci_ssh_key").write_text(encrypt(tmp_path, key.read_text()))
    public = key.with_suffix(".pub").read_text().strip()
    docker(
        "exec",
        container,
        "sh",
        "-c",
        f"echo '{public}' > /root/.ssh/authorized_keys && "
        "chmod 600 /root/.ssh/authorized_keys",
    )
    key.unlink()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "STARTUP_VAULT_PASSWORD": TEMPLATE_VAULT_PASSWORD,
        "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
        "UV_PROJECT_ENVIRONMENT": str(Path(sys.executable).parent.parent),
        "NO_COLOR": "1",
        # Real clusters listen on 22; the local master only differs in its port
        # and in keeping host keys out of the developer's known_hosts.
        "ANSIBLE_REMOTE_PORT": str(port),
        "ANSIBLE_SSH_ARGS": (
            f"-o UserKnownHostsFile={tmp_path / 'known_hosts'} "
            "-o StrictHostKeyChecking=accept-new"
        ),
    }
    _wait_for_ssh(port)
    return ctx.deployment_dir, container, env


def _wait_for_ssh(port):
    import socket

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise AssertionError("SSH master did not start")


def launch(deployment, env, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            *arguments,
            "--working-directory",
            str(deployment),
            "--environment",
            "production",
            "--repo-url",
            ROLES,
            "--version",
            ROLES_VERSION,
        ],
        cwd=deployment,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def remote(container, command):
    return docker("exec", container, "sh", "-c", command).stdout


def test_attached_startup_backs_up_only_its_own_namespace(attached, tmp_path):
    deployment, container, env = attached
    backups = tmp_path / "backups"
    # WHEN the attached startup's operator runs the public backup command.
    result = launch(deployment, env, "backup", "--backup-dir", str(backups))
    # THEN the owner's master returns the attached startup's data, and only that.
    assert result.returncode == 0, result.stdout + result.stderr
    [snapshot] = list(backups.iterdir())
    files = {path.name.split("-production-")[0]: path for path in snapshot.iterdir()}
    assert set(files) == {
        f"{ATTACHED}-db",
        f"{ATTACHED}-django",
        f"{ATTACHED}-media",
    }
    assert gzip.decompress(files[f"{ATTACHED}-db"].read_bytes()) == b"SECOND ROWS\n"
    assert (
        f"/srv/k8s/{ATTACHED}"
        in gzip.decompress(files[f"{ATTACHED}-django"].read_bytes()).decode()
    )
    with tarfile.open(files[f"{ATTACHED}-media"]) as media:
        owner = media.extractfile("media/owner.txt")
        assert owner is not None and owner.read() == ATTACHED.encode()
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files.values())
    calls = remote(container, "cat /srv/k8s/calls.log").splitlines()
    assert calls and all(call.startswith(f"namespace={ATTACHED} ") for call in calls)
    assert remote(container, "ls /tmp").strip() == ""
    assert not list(deployment.glob(".startup-data-*.yml"))


def test_attached_startup_restores_only_into_its_own_namespace(attached, tmp_path):
    deployment, container, env = attached
    dump = tmp_path / "restore.sql.gz"
    dump.write_bytes(gzip.compress(b"RESTORED ROWS;\n"))
    media_source = tmp_path / "media-source"
    (media_source / "media").mkdir(parents=True)
    (media_source / "media/restored.txt").write_text("restored")
    archive = tmp_path / "restore-media.tar.gz"
    with tarfile.open(archive, "w:gz") as media:
        media.add(media_source / "media", arcname="media")
    # WHEN the attached startup's operator restores a dump and a media archive.
    result = launch(
        deployment,
        env,
        "restore",
        "--yes",
        "--backup-dir",
        str(tmp_path),
        "--db-file",
        str(dump),
        "--media-file",
        str(archive),
    )
    # THEN its own database and media change; the owner's namespace is untouched.
    assert result.returncode == 0, result.stdout + result.stderr
    restored = remote(container, f"cat /srv/k8s/{ATTACHED}/psql.log")
    assert "RESTORED ROWS;" in restored
    assert "SET ROLE" in restored
    assert remote(container, f"ls /srv/k8s/{ATTACHED}/app/media").split() == [
        "restored.txt"
    ]
    assert remote(container, f"ls /srv/k8s/{OWNER}").split() == [
        "app",
        "db.sql",
        "secret",
    ]
    assert remote(container, f"cat /srv/k8s/{OWNER}/app/media/owner.txt") == OWNER
    calls = remote(container, "cat /srv/k8s/calls.log").splitlines()
    assert calls and all(call.startswith(f"namespace={ATTACHED} ") for call in calls)
    assert remote(container, "ls /tmp").strip() == ""
