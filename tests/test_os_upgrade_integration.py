"""GIVEN real projects and role repositories, WHEN the CLI runs, THEN guards hold."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SOURCE = Path(__file__).resolve().parents[1] / "src"
PASSWORD = "synthetic-integration-password"


def cli(project, env, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "os-upgrade",
            "--working-directory",
            str(project),
            "--environment",
            "production",
            "--target-version",
            "26.04",
            *arguments,
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "deployment"
    project.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "STARTUP_VAULT_PASSWORD": PASSWORD,
        "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
        "UV_PROJECT_ENVIRONMENT": str(Path(sys.executable).parent.parent),
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    inventory = {
        "all": {
            "children": {
                "production": {"hosts": {"node-a": {}, "node-b": {}}},
                "staging": {"hosts": {"other-environment": {}}},
            },
            "vars": {
                "ansible_connection": "local",
                "ansible_python_interpreter": sys.executable,
            },
        }
    }
    (project / "inventory.byos.yml").write_text(yaml.safe_dump(inventory))
    # Real Ansible Vault boundary; synthetic key is never used with local connection.
    # Encrypt stdin data separately from the password, using a private fixture file.
    password_file = tmp_path / "password"
    password_file.write_text(PASSWORD)
    password_file.chmod(0o600)
    encrypted = subprocess.run(
        [
            str(Path(sys.executable).parent / "ansible-vault"),
            "encrypt",
            "--vault-password-file",
            str(password_file),
            "--output",
            "-",
        ],
        input="synthetic-test-key\n",
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    password_file.unlink()
    assert encrypted.returncode == 0, encrypted.stderr
    (project / "ci_ssh_key").write_text(encrypted.stdout)
    source = tmp_path / "shared-repository"
    source.mkdir()
    (source / "roles").mkdir()
    (source / "ansible.cfg").write_text("[defaults]\n")
    # The shared repository is the external release boundary. This real playbook
    # records exactly the values and hosts received by the public CLI adapter.
    (source / "os-upgrade-playbook.yml").write_text("""---
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        dest: '{{ output_file }}'
        content: '{{ {"host": inventory_hostname, "target": os_upgrade_target, "environment": os_upgrade_environment, "execute": os_upgrade_execute, "backup": os_upgrade_backup_confirmed, "health": os_upgrade_health_urls} | to_json }}'
""")
    inventory["all"]["vars"]["output_file"] = str(
        tmp_path / "{{ inventory_hostname }}.json"
    )
    (project / "inventory.byos.yml").write_text(yaml.safe_dump(inventory))
    for arguments in [
        ("init", "-b", "main"),
        ("add", "."),
        ("commit", "-m", "reviewed shared release"),
    ]:
        subprocess.run(
            ["git", *arguments], cwd=source, env=env, check=True, capture_output=True
        )
    return project, source, env


def test_preview_clones_pins_and_restricts_hosts(project):
    # GIVEN a real BYOS project and local shared release, WHEN preview is limited
    # to one node, THEN only that production node is selected and roles are pinned.
    root, source, env = project
    result = cli(
        root, env, "--repo-url", source.as_uri(), "--limit", "node-a", "--dry-run"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preview only" in result.stdout
    data = json.loads((root.parent / "node-a.json").read_text())
    assert data == {
        "host": "node-a",
        "target": "26.04",
        "environment": "production",
        "execute": False,
        "backup": False,
        "health": [],
    }
    assert not (root.parent / "node-b.json").exists()
    assert not (root.parent / "other-environment.json").exists()
    assert (root / ".shared-roles/os-upgrade-playbook.yml").is_file()
    assert (root / "shared-roles.ref").is_file()
    assert (root / "shared-roles.sha256").is_file()
    assert PASSWORD not in result.stdout + result.stderr


def test_execution_forwards_explicit_backup_and_health_checks(project):
    # GIVEN explicit execution, verified backups and HTTPS checks, WHEN the CLI
    # runs, THEN the reviewed playbook receives those values unchanged.
    root, source, env = project
    result = cli(
        root,
        env,
        "--repo-url",
        source.as_uri(),
        "--limit",
        "node-a",
        "--execute",
        "--backup-confirmed",
        "--health-url",
        "https://example.com/health",
        "--health-url",
        "https://api.example.com/health",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((root.parent / "node-a.json").read_text())
    assert data["execute"] and data["backup"]
    assert data["health"] == [
        "https://example.com/health",
        "https://api.example.com/health",
    ]


@pytest.mark.parametrize(
    "flags,message",
    [
        (["--execute"], "--backup-confirmed"),
        (["--execute", "--backup-confirmed"], "--health-url"),
        (["--execute", "--dry-run"], "cannot be combined"),
        (["--target-version", "26.10"], "LTS version"),
        (["--limit", "node-a,other-environment"], "single host"),
        (
            ["--health-url", "https://user:secret@example.com"],
            "without embedded credentials",
        ),
    ],
)
def test_invalid_requests_fail_before_role_setup(project, flags, message):
    # GIVEN an unsafe or invalid request, WHEN the CLI process runs, THEN it
    # refuses before creating shared roles or running a remote playbook.
    root, _, env = project
    result = cli(root, env, *flags)
    assert result.returncode != 0 and message in result.stderr
    assert not (root / ".shared-roles").exists()
    assert not (root.parent / "node-a.json").exists()


def test_limit_cannot_select_other_environment(project):
    # GIVEN a host in another environment, WHEN --limit names it, THEN the CLI
    # reports an empty selection instead of running outside production.
    root, source, env = project
    result = cli(
        root, env, "--repo-url", source.as_uri(), "--limit", "other-environment"
    )
    assert result.returncode != 0 and "No VM hosts match" in result.stderr
    assert not (root.parent / "other-environment.json").exists()


def test_local_playbook_cannot_shadow_reviewed_shared_upgrade(project):
    # GIVEN a local same-named playbook, WHEN preview runs, THEN only the pinned
    # shared playbook executes and local instructions are ignored.
    root, source, env = project
    (root / "os-upgrade-playbook.yml").write_text(
        "- hosts: all\n  tasks:\n    - ansible.builtin.fail: {msg: unreviewed-local-playbook}\n"
    )
    result = cli(root, env, "--repo-url", source.as_uri(), "--limit", "node-a")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unreviewed-local-playbook" not in result.stdout + result.stderr
