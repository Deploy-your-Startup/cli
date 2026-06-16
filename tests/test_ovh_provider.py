"""Tests for the OVH / cloud_provider switch (CLI side)."""

from pathlib import Path

import pytest

from cli import ansible_commands
from cli.ovh import validate_and_normalize_clouds_yaml
from cli.wizard.context import BootstrapContext
from cli.wizard.runner import steps_for
from cli.wizard.steps.project import _set_ovh_group_vars


def _ctx(cloud_provider: str) -> BootstrapContext:
    return BootstrapContext(
        project_name="p",
        base_domain="d",
        additional_domains="",
        github_username="g",
        postgres_version="17",
        sentry_dsn="",
        output_dir=Path("/tmp"),
        kind="fullstack",
        cloud_provider=cloud_provider,
    )


def test_clouds_yaml_renames_single_entry_to_ovh():
    raw = (
        "clouds:\n"
        "  openstack:\n"
        "    auth:\n"
        "      auth_url: https://auth.example/v3\n"
        "      application_credential_id: x\n"
        "      application_credential_secret: y\n"
        "    region_name: GRA11\n"
    )
    out = validate_and_normalize_clouds_yaml(raw)
    assert out is not None
    assert "ovh:" in out
    assert "openstack:" not in out


def test_clouds_yaml_keeps_existing_ovh_entry():
    raw = "clouds:\n  ovh:\n    auth:\n      auth_url: https://a/v3\n"
    out = validate_and_normalize_clouds_yaml(raw)
    assert out is not None and "ovh:" in out


def test_clouds_yaml_rejects_missing_auth_url():
    assert validate_and_normalize_clouds_yaml("clouds:\n  x:\n    auth: {}\n") is None
    assert validate_and_normalize_clouds_yaml("not: a cloudsfile\n") is None
    assert validate_and_normalize_clouds_yaml(": : :\n") is None


def test_read_cloud_provider_default_and_explicit(tmp_path):
    gv = tmp_path / "group_vars"
    gv.mkdir()
    # Missing file -> hetzner default
    assert ansible_commands._read_cloud_provider(tmp_path) == "hetzner"
    (gv / "all.yml").write_text("cloud_provider: ovh\nfoo: bar\n", encoding="utf-8")
    assert ansible_commands._read_cloud_provider(tmp_path) == "ovh"


def test_read_cloud_provider_rejects_unknown(tmp_path):
    gv = tmp_path / "group_vars"
    gv.mkdir()
    (gv / "all.yml").write_text("cloud_provider: aws\n", encoding="utf-8")
    with pytest.raises(Exception, match="Unsupported cloud_provider"):
        ansible_commands._read_cloud_provider(tmp_path)


def test_apply_cloud_credentials_ovh(monkeypatch, tmp_path):
    gv = tmp_path / "group_vars"
    gv.mkdir()
    (gv / "all.yml").write_text("cloud_provider: ovh\n", encoding="utf-8")

    fake_clouds = tmp_path / "clouds-xyz.yaml"
    fake_clouds.write_text("clouds: {}\n")
    monkeypatch.setattr(
        ansible_commands,
        "_write_temp_openstack_clouds",
        lambda *a, **k: fake_clouds,
    )

    env: dict[str, str] = {}
    inventory_args, cleanup = ansible_commands._apply_cloud_credentials(
        env,
        working_directory=str(tmp_path),
        vault_password="pw",
        environment="production",
        working_dir=tmp_path,
        shared_dir=".shared-roles",
    )

    assert env["OS_CLIENT_CONFIG_FILE"] == str(fake_clouds)
    assert "HCLOUD_TOKEN" not in env
    assert inventory_args == [
        "-i",
        ".shared-roles/inventory.openstack.yml",
        "-i",
        ".shared-roles/inventory.ini",
    ]
    cleanup()
    assert not fake_clouds.exists()


def test_apply_cloud_credentials_hetzner_default(monkeypatch, tmp_path):
    monkeypatch.setattr(ansible_commands, "get_hcloud_token", lambda *a, **k: "tok")
    env: dict[str, str] = {}
    inventory_args, cleanup = ansible_commands._apply_cloud_credentials(
        env,
        working_directory=str(tmp_path),
        vault_password="pw",
        environment="production",
        working_dir=tmp_path,
        shared_dir=".shared-roles",
    )
    assert env == {"HCLOUD_TOKEN": "tok"}
    assert inventory_args == []
    cleanup()  # no-op


def test_run_infrastructure_ovh_uses_openstack_inventory(monkeypatch, tmp_path):
    working_dir = tmp_path / "deployment"
    (working_dir / "group_vars").mkdir(parents=True)
    (working_dir / "group_vars" / "all.yml").write_text(
        "cloud_provider: ovh\n", encoding="utf-8"
    )
    fake_clouds = tmp_path / "clouds.yaml"
    fake_clouds.write_text("clouds: {}\n")

    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)
    monkeypatch.setattr(
        ansible_commands, "_write_temp_openstack_clouds", lambda *a, **k: fake_clouds
    )
    monkeypatch.setattr(ansible_commands, "_find_uv", lambda: "uv")
    monkeypatch.setattr(ansible_commands, "_ansible_env", lambda *a: {"BASE": "1"})
    monkeypatch.setattr(ansible_commands, "ansible_bin", lambda name: name)

    recorded = {}

    def fake_run(command, *, cwd, env=None, input_text=None, capture_output=False):
        recorded["command"] = command
        recorded["env"] = env

    monkeypatch.setattr(ansible_commands, "_run_command", fake_run)

    ansible_commands.run_infrastructure(
        vault_password="secret",
        environment="production",
        working_directory=str(working_dir),
    )

    assert recorded["env"]["OS_CLIENT_CONFIG_FILE"] == str(fake_clouds)
    assert "HCLOUD_TOKEN" not in recorded["env"]
    cmd = recorded["command"]
    assert "-i" in cmd
    assert ".shared-roles/inventory.openstack.yml" in cmd
    assert ".shared-roles/inventory.ini" in cmd
    # temp clouds.yaml cleaned up after the run
    assert not fake_clouds.exists()


def test_set_ovh_group_vars(tmp_path):
    gv = tmp_path / "group_vars"
    gv.mkdir()
    all_yml = gv / "all.yml"
    all_yml.write_text(
        "cloud_provider: hetzner\nmanage_dns: true\nother: 1\n", encoding="utf-8"
    )
    _set_ovh_group_vars(tmp_path)
    text = all_yml.read_text()
    assert "cloud_provider: ovh" in text
    assert "manage_dns: false" in text
    assert "other: 1" in text


def test_steps_for_selects_provider_step():
    assert "HetznerStep" in [s.__name__ for s in steps_for(_ctx("hetzner"))]
    assert "OvhStep" in [s.__name__ for s in steps_for(_ctx("ovh"))]
