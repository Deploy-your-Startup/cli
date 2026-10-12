"""GIVEN an attached startup with a DNS token in its Vault, WHEN `startup ansible dns`
runs, THEN the shared DNS playbook gets the cluster address, the project's domain
and the token, and nothing else is provisioned."""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from tests.vault_support import RealVault

ROOT = Path(__file__).resolve().parents[1]
VAULT_PASSWORD = "integration-only"

# Stand-in for the shared dns-playbook.yml: records what the CLI hands to it.
DNS_PLAYBOOK = """
- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        dest: "{{ lookup('env', 'DNS_RECORD_FILE') }}"
        content: "{{ {'ip': dns_target_ip, 'domain': base_domain,
          'extra': additional_domains, 'token': lookup('env', 'HCLOUD_TOKEN')} | to_json }}"
"""


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def shared_roles_repo(tmp_path):
    origin = tmp_path / "deploy-your-startup"
    (origin / "roles" / "placeholder").mkdir(parents=True)
    (origin / "roles" / "placeholder" / "README.md").write_text("roles\n")
    (origin / "requirements.yml").write_text("---\ncollections: []\n")
    (origin / "dns-playbook.yml").write_text(DNS_PLAYBOOK)
    git("init", "-q", "-b", "main", cwd=origin)
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A", cwd=origin)
    git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "roles",
        cwd=origin,
    )
    return origin


def attached_project(tmp_path, *, inventory=True):
    deployment = tmp_path / "sample" / "deployment"
    (deployment / "group_vars").mkdir(parents=True)
    (deployment / "group_vars" / "all.yml").write_text("project_name: sample\n")
    (deployment / "group_vars" / "production.yml").write_text(
        "base_domain: sample.de\nadditional_domains: []\n"
    )
    (deployment / "hcloud_token_production").write_bytes(
        RealVault(VAULT_PASSWORD).encrypt("dns-only-token")
    )
    if inventory:
        (deployment / "inventory.byos.yml").write_text(
            yaml.safe_dump(
                {
                    "all": {
                        "children": {
                            "hcloud": {
                                "children": {
                                    "hcloud_type_master": {
                                        "hosts": {
                                            "owner-master-0": {
                                                "ansible_host": "203.0.113.10"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            )
        )
    return deployment


def run_dns(tmp_path, deployment, origin):
    # `uv run --project <deployment>` resolves the project's own environment;
    # the stand-in runs the requested tool from this test's environment instead.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    (bin_dir / "uv").chmod(0o755)
    record = tmp_path / "dns-run.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "dns",
            "--environment",
            "production",
            "--working-directory",
            str(deployment),
            "--vault-password",
            VAULT_PASSWORD,
            "--repo-url",
            str(origin),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(ROOT / "src"),
            "DNS_RECORD_FILE": str(record),
            "NO_COLOR": "1",
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    return result, record


def test_dns_run_hands_cluster_address_domain_and_token_to_the_playbook(tmp_path):
    # GIVEN an attached startup and the owner's shared roles.
    origin = shared_roles_repo(tmp_path)
    deployment = attached_project(tmp_path)
    # WHEN its operator runs the DNS command.
    result, record = run_dns(tmp_path, deployment, origin)
    # THEN the playbook targets the cluster master with the startup's own domain and token.
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(record.read_text()) == {
        "ip": "203.0.113.10",
        "domain": "sample.de",
        "extra": [],
        "token": "dns-only-token",
    }


def test_provisioned_project_is_sent_to_the_infrastructure_command(tmp_path):
    # GIVEN a Hetzner-provisioned project without a static inventory.
    origin = shared_roles_repo(tmp_path)
    deployment = attached_project(tmp_path, inventory=False)
    # WHEN the DNS command runs.
    result, record = run_dns(tmp_path, deployment, origin)
    # THEN it explains where DNS is managed and runs nothing.
    assert result.returncode != 0
    assert "startup ansible infrastructure" in result.stderr
    assert not record.exists()
    assert not (deployment / ".shared-roles").exists()
