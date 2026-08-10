import click
import pytest
from click.testing import CliRunner

from cli import ansible_commands
from cli.startup import cli


def _patch_playbook_run(monkeypatch, recorded):
    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)
    monkeypatch.setattr(ansible_commands, "get_hcloud_token", lambda *args: "token")
    monkeypatch.setattr(ansible_commands, "_find_uv", lambda: "uv")
    monkeypatch.setattr(ansible_commands, "_ansible_env", lambda *args: {"BASE": "1"})
    monkeypatch.setattr(ansible_commands, "ansible_bin", lambda name: name)

    def fake_run(command, *, cwd, env=None, input_text=None, capture_output=False):
        recorded["command"] = command
        recorded["cwd"] = cwd
        recorded["env"] = env
        recorded["input_text"] = input_text

    monkeypatch.setattr(ansible_commands, "_run_command", fake_run)


def test_run_os_upgrade_executes_playbook(monkeypatch, tmp_path):
    working_dir = tmp_path / "deployment"
    working_dir.mkdir()
    playbook_path = working_dir / "os-upgrade-playbook.yml"
    playbook_path.write_text("- hosts: all\n", encoding="utf-8")

    recorded = {}
    _patch_playbook_run(monkeypatch, recorded)

    ansible_commands.run_os_upgrade(
        vault_password="secret",
        environment="production",
        working_directory=str(working_dir),
        limit="web",
    )

    assert recorded["cwd"] == working_dir.resolve()
    assert recorded["input_text"] == "secret"
    assert recorded["env"] == {"BASE": "1", "HCLOUD_TOKEN": "token"}
    assert recorded["command"] == [
        "uv",
        "run",
        "--project",
        str(working_dir.resolve()),
        "ansible-playbook",
        str(playbook_path.resolve()),
        "--vault-password-file",
        "/bin/cat",
        "-l",
        "production,web",
        "--extra-vars",
        '{"os_upgrade": true}',
    ]


def test_run_os_upgrade_single_node_stays_opt_in(monkeypatch, tmp_path):
    """The guard only relaxes when asked. A default run must not carry the flag,
    or the role's single-node refusal never fires."""
    working_dir = tmp_path / "deployment"
    working_dir.mkdir()
    (working_dir / "os-upgrade-playbook.yml").write_text("- hosts: all\n", "utf-8")

    recorded = {}
    _patch_playbook_run(monkeypatch, recorded)

    ansible_commands.run_os_upgrade(
        vault_password="secret",
        environment="production",
        working_directory=str(working_dir),
        allow_single_node=True,
    )

    assert '{"os_upgrade": true, "os_upgrade_allow_single_node": true}' in (
        recorded["command"]
    )


def test_run_os_upgrade_reminds_about_the_image_pin(monkeypatch, tmp_path, capsys):
    """The upgrade moves existing nodes but not hetzner_os_image, so new servers
    would silently come up on the old release."""
    working_dir = tmp_path / "deployment"
    working_dir.mkdir()
    (working_dir / "os-upgrade-playbook.yml").write_text("- hosts: all\n", "utf-8")

    _patch_playbook_run(monkeypatch, {})

    ansible_commands.run_os_upgrade(
        vault_password="secret",
        environment="production",
        working_directory=str(working_dir),
    )

    assert "hetzner_os_image" in capsys.readouterr().out


def test_run_os_upgrade_requires_valid_environment(monkeypatch):
    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)

    with pytest.raises(
        click.ClickException, match="--environment must be production or staging"
    ):
        ansible_commands.run_os_upgrade(
            vault_password="secret",
            environment="dev",
        )


def test_ansible_os_upgrade_cli_forwards_flags(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        "cli.ansible_commands.resolve_vault_password",
        lambda **kwargs: "resolved-secret",
    )

    def fake_run_os_upgrade(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr("cli.ansible_commands.run_os_upgrade", fake_run_os_upgrade)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ansible",
            "os-upgrade",
            "--vault-password",
            "secret",
            "--environment",
            "production",
            "--working-directory",
            "/tmp/project",
            "--playbook",
            "custom-os.yml",
            "--allow-single-node",
            "--limit",
            "workers",
            "--shared-dir",
            ".roles",
            "--version",
            "stable",
            "--no-refresh",
            "--repo-url",
            "https://github.com/example/deploy-your-startup",
        ],
    )

    assert result.exit_code == 0
    assert calls == {
        "vault_password": "resolved-secret",
        "environment": "production",
        "working_directory": "/tmp/project",
        "playbook": "custom-os.yml",
        "allow_single_node": True,
        "limit": "workers",
        "shared_dir": ".roles",
        "version": "stable",
        "refresh": False,
        "repo_url": "https://github.com/example/deploy-your-startup",
    }
