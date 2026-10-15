"""BYOS finalization through real Git and SSH; GitHub/Keychain stay local."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_shared_cluster_data_integration import docker, master

ssh_master = master
ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kwargs)


@pytest.mark.parametrize("authorized", [True, False])
def test_byos_finalize_and_summary(tmp_path, ssh_master, authorized):
    # GIVEN a real Git project and SSH server with or without operator access.
    container, port = ssh_master
    project = tmp_path / "demo"
    deployment = project / "deployment"
    deployment.mkdir(parents=True)
    bare = tmp_path / "remote.git"
    run("git", "init", "--bare", str(bare))
    run("git", "init", "-b", "main", str(project))
    run("git", "-C", str(project), "config", "user.name", "Test")
    run("git", "-C", str(project), "config", "user.email", "test@example.com")
    run(
        "git",
        "-C",
        str(project),
        "config",
        f"url.{bare.as_uri()}.insteadOf",
        "https://github.com/example/demo.git",
    )
    operator = tmp_path / "operator"
    deploy = tmp_path / "deploy"
    for key in (operator, deploy):
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
    public = deploy.with_suffix(".pub").read_text()
    (deployment / "byos_deploy_key.pub").write_text(public)
    (project / ".gitignore").write_text("deployment/byos_deploy_key.pub\n")
    (deployment / "README").write_text("Disposable finalization fixture\n")
    docker(
        "exec",
        "-i",
        container,
        "sh",
        "-c",
        "cat > /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys",
        input=operator.with_suffix(".pub").read_text() if authorized else "",
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ssh = shutil.which("ssh")
    (binaries / "ssh").write_text(
        f'#!/bin/sh\nexec "{ssh}" -F /dev/null -p {port} -i "{operator}" '
        f'-o IdentitiesOnly=yes -o IdentityAgent=none -o UserKnownHostsFile="{tmp_path / "known_hosts"}" "$@"\n'
    )
    calls = tmp_path / "calls"
    (binaries / "gh").write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as out:
    out.write(json.dumps(args) + '\\n')
if args[:2] == ['secret', 'set']:
    assert sys.stdin.read() == 'synthetic-password'
elif args[:2] == ['workflow', 'run']:
    assert Path(os.environ['TEST_REMOTE'], 'refs/heads/main').exists()
elif args[:2] not in (['repo', 'view'], ['api', '-X']):
    sys.exit(1)
""")
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "TEST_CALLS": str(calls),
        "TEST_REMOTE": str(bare),
        "NO_COLOR": "1",
    }
    script = r"""
import sys, types
from pathlib import Path
import click
from cli.wizard.context import BootstrapContext
from cli.wizard.steps.byos import ByosStep
from cli.wizard.steps.finalize import FinalizeStep
from cli.wizard_output import summary_box
# Replace only the OS Keychain boundary, never CLI code.
module = types.ModuleType('keyring.backends.macOS')
class Keyring:
    def set_password(self, *args): pass
module.Keyring = Keyring
sys.modules['keyring.backends.macOS'] = module
sys.platform = 'darwin'
ctx = BootstrapContext('demo', 'demo.example', '', 'example', '17', '', Path.cwd(),
                       provider='byos', byos_host='127.0.0.1', non_interactive=True,
                       vault_password='synthetic-password')
try:
    ByosStep().run(ctx)
    FinalizeStep().run(ctx)
    summary_box(project_name='demo', project_dir=str(ctx.project_dir),
                github_url=ctx.github_url, domain=ctx.base_domain, provider='byos')
except click.ClickException as exc:
    exc.show()
    sys.exit(1)
"""
    # WHEN the real finalization runs as a process (twice to test idempotence).
    for _ in range(2 if authorized else 1):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        output = result.stdout + result.stderr
        recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        # THEN success installs the key and requests CI without manual deploy steps.
        assert "./make.sh" not in output
        assert "synthetic-password" not in output
        if authorized:
            assert result.returncode == 0, output
            assert "installed" in output
            assert "Your existing server (BYOS)" in output
            assert "https://demo.example" in output
            assert "https://github.com/example/demo/actions" in output
            assert "Not verified yet" in output
            assert "Next steps" not in output
            assert "printf" not in output
            assert [
                "workflow",
                "run",
                "deploy-infrastructure.yml",
                "--ref",
                "main",
            ] in recorded
            keys = docker("exec", container, "cat", "/root/.ssh/authorized_keys").stdout
            assert keys.splitlines().count(public.strip()) == 1
            # The new CI identity can really authenticate, independently of the operator.
            run(
                ssh,
                "-F",
                "/dev/null",
                "-p",
                str(port),
                "-i",
                str(deploy),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "IdentityAgent=none",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "root@127.0.0.1",
                "true",
            )
        else:
            assert result.returncode == 1
            assert "then rerun bootstrap" in output
            assert "authorized_keys" in output
            assert not (bare / "refs/heads/main").exists()
            assert not any(call[:2] == ["workflow", "run"] for call in recorded)
            assert "Setup complete" not in output


@pytest.mark.parametrize(
    "kind,provider,owner",
    [
        ("fullstack", "byos", None),
        ("fullstack", "hetzner", None),
        ("pitch", "hetzner", None),
        ("fullstack", "byos", "example/owner"),
    ],
)
def test_completion_summary_links(tmp_path, kind, provider, owner):
    # GIVEN a configured project whose local path exceeds a narrow terminal.
    folder = str(tmp_path / ("long-folder-" * 8))
    script = (
        "from cli.wizard_output import summary_box\n"
        f"summary_box(project_name='demo', project_dir={folder!r}, "
        "github_url='https://github.com/example/demo', domain='demo.example', "
        f"kind={kind!r}, provider={provider!r}, cluster_owner={owner!r}, "
        "keychain_service='VAULT_PASSWORD_DEMO')"
    )
    # WHEN the actual summary runs in a plain, narrow terminal environment.
    result = run(
        sys.executable,
        "-c",
        script,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "NO_COLOR": "1",
            "COLUMNS": "40",
        },
    )
    # THEN complete links and honest status replace the setup checklist.
    for value in (
        folder,
        "https://demo.example",
        "https://github.com/example/demo",
        "https://github.com/example/demo/actions",
        "VAULT_PASSWORD_DEMO",
        "Requested via GitHub Actions",
        "Not verified yet",
    ):
        assert value in result.stdout
    assert "./make.sh" not in result.stdout
    assert "Copy the deploy key" not in result.stdout
    assert "Next" not in result.stdout
    if kind == "pitch":
        assert "Cloudflare Pages" in result.stdout
    elif owner:
        assert owner in result.stdout
    else:
        assert ("BYOS" if provider == "byos" else "Hetzner Cloud") in result.stdout
