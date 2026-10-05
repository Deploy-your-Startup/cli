import click
import pytest
from click.testing import CliRunner

from cli import ansible_commands
from cli.startup import cli


def _record_playbook_run(monkeypatch, *, byos=False):
    recorded = {}

    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)
    monkeypatch.setattr(ansible_commands, "get_hcloud_token", lambda *args: "token")
    monkeypatch.setattr(ansible_commands, "_find_uv", lambda: "uv")
    monkeypatch.setattr(ansible_commands, "_ansible_env", lambda *args: {"BASE": "1"})
    monkeypatch.setattr(ansible_commands, "ansible_bin", lambda name: name)
    monkeypatch.setattr(ansible_commands, "_is_byos", lambda working_dir: byos)
    monkeypatch.setattr(
        ansible_commands,
        "_dynamic_inventory_hostvars",
        lambda *args: {"production-master-0": {}},
    )
    monkeypatch.setattr(ansible_commands, "_ensure_tailnet", lambda *a, **k: None)

    def fake_run(command, *, cwd, env=None, input_text=None, capture_output=False):
        recorded["command"] = command
        recorded["env"] = env
        recorded["input_text"] = input_text

    def fake_byos(working_directory, vault_password, shared_dir, **kwargs):
        recorded["byos"] = kwargs

    monkeypatch.setattr(ansible_commands, "_run_command", fake_run)
    monkeypatch.setattr(ansible_commands, "_run_byos_playbook", fake_byos)
    return recorded


def test_cert_manager_upgrade_runs_project_playbook_with_upgrade_flag(
    monkeypatch, tmp_path
):
    # GIVEN a hetzner project
    recorded = _record_playbook_run(monkeypatch)

    # WHEN the upgrade runs without an explicit version
    ansible_commands.run_cert_manager_upgrade(
        vault_password="secret",
        environment="production",
        working_directory=str(tmp_path),
    )

    # THEN the project's playbook runs only the cert-manager role, opted in
    assert recorded["input_text"] == "secret"
    assert recorded["env"] == {"BASE": "1", "HCLOUD_TOKEN": "token"}
    assert recorded["command"] == [
        "uv",
        "run",
        "--project",
        str(tmp_path.resolve()),
        "ansible-playbook",
        "playbook.yml",
        "--vault-password-file",
        "/bin/cat",
        "--tags",
        "cert-manager",
        "--skip-tags",
        "infrastructure",
        "--extra-vars",
        '{"cert_manager_upgrade": true}',
    ]


def test_cert_manager_upgrade_forwards_target_version(monkeypatch, tmp_path):
    # GIVEN a hetzner project
    recorded = _record_playbook_run(monkeypatch)

    # WHEN a target version is given
    ansible_commands.run_cert_manager_upgrade(
        vault_password="secret",
        environment="production",
        working_directory=str(tmp_path),
        cert_manager_version="v1.21.2",
    )

    # THEN it overrides the pinned chart version
    assert recorded["command"][-2:] == [
        "--extra-vars",
        '{"cert_manager_upgrade": true, "cert_manager_chart_version": "v1.21.2"}',
    ]


def test_cert_manager_upgrade_on_byos_passes_extra_vars(monkeypatch, tmp_path):
    # GIVEN a bring-your-own-server project
    recorded = _record_playbook_run(monkeypatch, byos=True)

    # WHEN the upgrade runs
    ansible_commands.run_cert_manager_upgrade(
        vault_password="secret",
        environment="production",
        working_directory=str(tmp_path),
    )

    # THEN the static-inventory run carries the same tags and opt-in
    assert recorded["byos"] == {
        "tags": ["cert-manager"],
        "skip_tags": ["infrastructure"],
        "extra_vars": '{"cert_manager_upgrade": true}',
    }


def test_cert_manager_upgrade_requires_valid_environment(monkeypatch):
    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)

    with pytest.raises(
        click.ClickException, match="--environment must be production or staging"
    ):
        ansible_commands.run_cert_manager_upgrade(
            vault_password="secret",
            environment="dev",
        )


def test_deploy_without_extra_vars_is_unchanged(monkeypatch, tmp_path):
    # GIVEN a plain deploy
    recorded = _record_playbook_run(monkeypatch)

    # WHEN it runs
    ansible_commands.run_deploy(
        "secret", "production", "backend", working_directory=str(tmp_path)
    )

    # THEN no --extra-vars are added
    assert "--extra-vars" not in recorded["command"]
    assert recorded["command"][-4:] == [
        "--tags",
        "backend",
        "--skip-tags",
        "infrastructure",
    ]


def test_ansible_cert_manager_upgrade_cli_forwards_flags(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        "cli.ansible_commands.resolve_vault_password",
        lambda **kwargs: "resolved-secret",
    )
    monkeypatch.setattr(
        "cli.ansible_commands.run_cert_manager_upgrade",
        lambda **kwargs: calls.update(kwargs),
    )

    result = CliRunner().invoke(
        cli,
        [
            "ansible",
            "cert-manager-upgrade",
            "--vault-password",
            "secret",
            "--environment",
            "production",
            "--working-directory",
            "/tmp/project",
            "--cert-manager-version",
            "v1.21.2",
            "--shared-dir",
            ".roles",
            "--version",
            "stable",
            "--no-refresh",
            "--repo-url",
            "https://github.com/example/deploy-your-startup",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == {
        "vault_password": "resolved-secret",
        "environment": "production",
        "working_directory": "/tmp/project",
        "cert_manager_version": "v1.21.2",
        "shared_dir": ".roles",
        "version": "stable",
        "refresh": False,
        "repo_url": "https://github.com/example/deploy-your-startup",
    }
