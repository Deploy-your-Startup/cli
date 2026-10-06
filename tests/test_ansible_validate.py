"""GIVEN local playbooks, WHEN the real CLI validates, THEN syntax gates work."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


def run(tmp_path, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "validate",
            "--working-directory",
            str(tmp_path),
            "--inventory",
            "inventory.ini",
            *arguments,
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
        },
        timeout=60,
    )


@pytest.mark.parametrize("valid", [True, False])
def test_real_ansible_checks_role_syntax_without_cloud_or_vault(tmp_path, valid):
    # GIVEN a local role and static inventory, with Keychain access disabled.
    (tmp_path / "inventory.ini").write_text("localhost ansible_connection=local\n")
    tasks = tmp_path / "roles" / "sample" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(
        "- ansible.builtin.debug:\n    msg: ready\n"
        if valid
        else "- this_module_does_not_exist: {}\n"
    )
    (tmp_path / "playbook.yml").write_text("- hosts: all\n  roles: [sample]\n")
    # WHEN the real startup CLI drives the real Ansible parser.
    result = run(tmp_path, "--playbook", "playbook.yml", "--roles-path", "roles")
    # THEN a valid role succeeds and invalid syntax fails without refreshing roles.
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert not (tmp_path / ".shared-roles").exists()


def test_missing_files_fail_before_launch(tmp_path):
    # GIVEN no playbook, WHEN validated, THEN the shell receives a useful failure.
    result = run(tmp_path, "--playbook", "missing.yml")
    assert result.returncode != 0
    assert "Playbook file not found" in result.stderr
