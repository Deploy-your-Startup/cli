"""GIVEN real projects, WHEN package updates run, THEN the CLI contract holds."""

import json
import subprocess
import sys

import pytest

from tests.test_os_upgrade_integration import project as project


def run_update(root, env, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "update-vms",
            "--working-directory",
            str(root),
            "--environment",
            "production",
            *arguments,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("custom", [False, True])
def test_package_update_runs_real_playbook_with_forwarded_flags(project, custom):
    # GIVEN a real shared repository and BYOS inventory, WHEN update-vms runs
    # with the documented flags, THEN Ansible sees the reboot/environment values.
    root, source, env = project
    content = """---
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        dest: '{{ output_file }}'
        content: '{{ {"environment": update_environment, "reboot": update_reboot} | to_json }}'
"""
    (source / "update-vms-playbook.yml").write_text(content)
    for args in [("add", "."), ("commit", "-m", "shared package update playbook")]:
        subprocess.run(
            ["git", *args], cwd=source, env=env, capture_output=True, check=True
        )
    setup = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "setup_ansible",
            "--working-directory",
            str(root),
            "--shared-dir",
            ".roles",
            "--repo-url",
            source.as_uri(),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr
    flags = [
        "--repo-url",
        source.as_uri(),
        "--shared-dir",
        ".roles",
        "--version",
        "main",
        "--no-refresh",
        "--limit",
        "node-a",
        "--reboot",
    ]
    if custom:
        (root / "custom-update.yml").write_text(content)
        flags.extend(["--playbook", "custom-update.yml"])
    result = run_update(root, env, *flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((root.parent / "node-a.json").read_text()) == {
        "environment": "production",
        "reboot": True,
    }
    assert "synthetic-integration-password" not in result.stdout + result.stderr


def test_package_update_rejects_invalid_environment_before_setup(project):
    # GIVEN an unsupported environment, WHEN the real CLI runs, THEN it exits
    # before installing roles or contacting any node.
    root, _, env = project
    result = run_update(root, env, "--environment", "dev")
    assert result.returncode != 0
    assert "--environment must be production or staging" in result.stderr
    assert not (root / ".shared-roles").exists()
