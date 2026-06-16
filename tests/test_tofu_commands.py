import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parents[1] / "src"))

from cli import tofu_commands


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_load_group_vars_merges_and_ignores_vault(tmp_path):
    _write(
        tmp_path / "group_vars" / "all.yml",
        """
project_name: "demo"
create_load_balancer: false
ssh_public_keys:
  - name: "demo_ci_key"
    key: "ssh-ed25519 AAAA ci"
secret_blob: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  3030
""",
    )
    _write(
        tmp_path / "group_vars" / "production.yml",
        """
base_domain: "example.com"
additional_domains: ["www.example.com"]
master_count: 1
worker_count: 0
""",
    )

    data = tofu_commands._load_group_vars(tmp_path, "production")

    assert data["project_name"] == "demo"
    assert data["base_domain"] == "example.com"
    assert data["master_count"] == 1
    # !vault-tagged values are ignored (None), never raise.
    assert data["secret_blob"] is None


def test_tofu_var_env_maps_and_json_encodes():
    group_vars = {
        "project_name": "demo",
        "base_domain": "example.com",
        "additional_domains": ["www.example.com"],
        "create_load_balancer": False,
        "master_count": 1,
        "worker_count": 2,
        "ssh_public_keys": [{"name": "k", "key": "ssh-ed25519 AAAA"}],
        "irrelevant": "dropped",
    }

    env = tofu_commands._tofu_var_env(group_vars)

    # Scalars stay as plain strings, complex values are JSON.
    assert env["TF_VAR_project_name"] == "demo"
    assert env["TF_VAR_base_domain"] == "example.com"
    assert env["TF_VAR_additional_domains"] == '["www.example.com"]'
    assert env["TF_VAR_create_load_balancer"] == "false"
    assert env["TF_VAR_master_count"] == "1"
    assert env["TF_VAR_worker_count"] == "2"
    assert '"name": "k"' in env["TF_VAR_ssh_public_keys"]
    # Unmapped keys are not forwarded.
    assert "TF_VAR_irrelevant" not in env


def test_tofu_var_env_requires_core_vars():
    with pytest.raises(Exception):
        tofu_commands._tofu_var_env({"base_domain": "example.com"})
    with pytest.raises(Exception):
        tofu_commands._tofu_var_env({"project_name": "demo"})
