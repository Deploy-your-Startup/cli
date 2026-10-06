"""GIVEN a local cluster stand-in, WHEN provisioning, THEN authorization is per run."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.vault_support import RealVault

SOURCE = Path(__file__).resolve().parents[1] / "src"
PASSWORD = "test-only-integration-password"


def run(tmp_path, *arguments, input=None):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "infrastructure",
            "--environment",
            "production",
            "--working-directory",
            str(tmp_path),
            *arguments,
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        input=input,
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
        },
        timeout=60,
    )


@pytest.mark.parametrize("byos", [False, True])
@pytest.mark.parametrize(
    "arguments,authorized",
    [([], False), (["--yes"], False), (["--allow-worker-teardown", "--yes"], True)],
)
def test_real_provisioning_overrides_permanent_authorization(
    tmp_path, byos, arguments, authorized
):
    # GIVEN exported roles and local inventory replacing the cloud/SSH boundary.
    shared = tmp_path / ".shared-roles"
    (shared / "roles").mkdir(parents=True)
    inventory = "[production]\nlocalhost ansible_connection=local\n"
    (tmp_path / "inventory.ini").write_text(inventory)
    (tmp_path / "ansible.cfg").write_text("[defaults]\ninventory = inventory.ini\n")
    if byos:
        (tmp_path / "inventory.byos.yml").write_text(
            "all:\n  children:\n    production:\n      hosts:\n        localhost:\n          ansible_connection: local\n"
        )
    variable_dir = tmp_path / "group_vars"
    variable_dir.mkdir()
    (variable_dir / "all.yml").write_text(
        "allow_worker_teardown: true\nnetwork_mode: public\n"
    )
    vault = RealVault(PASSWORD)
    (tmp_path / ("ci_ssh_key" if byos else "hcloud_token_production")).write_bytes(
        vault.encrypt(b"integration-only-boundary-value")
    )
    output = tmp_path / "authorization.json"
    (tmp_path / "playbook.yml").write_text(
        '- hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.copy:\n        content: "{{ {\'allowed\': allow_worker_teardown} | to_json }}"\n        dest: "'
        + str(output)
        + '"\n      tags: infrastructure\n'
    )
    # WHEN the real CLI invokes the real playbook with real Vault crypto.
    result = run(tmp_path, "--no-refresh", "--vault-password", PASSWORD, *arguments)
    # THEN group_vars cannot authorize deletion and --yes alone cannot either.
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text()) == {"allowed": authorized}


def test_declining_stops_before_keychain_or_cloud(tmp_path):
    # GIVEN no secrets or infrastructure, WHEN declining, THEN only confirmation runs.
    result = run(tmp_path, "--allow-worker-teardown", input="n\n")
    assert result.returncode != 0
    assert "local data" in result.stdout
    assert "No vault password provided" not in result.stderr
    assert not (tmp_path / ".shared-roles").exists()
