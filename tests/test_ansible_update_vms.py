from pathlib import Path

from click.testing import CliRunner

from cli import ansible_commands
from cli.startup import cli


def test_run_update_vms_executes_playbook(monkeypatch, tmp_path):
    working_dir = tmp_path / "deployment"
    working_dir.mkdir()
    playbook_path = working_dir / "update-vms-playbook.yml"
    playbook_path.write_text("- hosts: all\n", encoding="utf-8")

    recorded = {}

    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)
    monkeypatch.setattr(ansible_commands, "get_hcloud_token", lambda *args: "token")
    monkeypatch.setattr(ansible_commands, "_find_uv", lambda: "uv")
    monkeypatch.setattr(ansible_commands, "_ansible_env", lambda *args: {"BASE": "1"})

    def fake_run(command, *, cwd, env=None, input_text=None, capture_output=False):
        recorded["command"] = command
        recorded["cwd"] = cwd
        recorded["env"] = env
        recorded["input_text"] = input_text
        recorded["capture_output"] = capture_output

    monkeypatch.setattr(ansible_commands, "_run_command", fake_run)

    ansible_commands.run_update_vms(
        vault_password="secret",
        environment="production",
        working_directory=str(working_dir),
        limit="web",
        reboot=True,
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
        '{"update_reboot": true, "update_environment": "production"}',
    ]


def test_run_update_vms_requires_valid_environment(monkeypatch):
    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)

    try:
        ansible_commands.run_update_vms(
            vault_password="secret",
            environment="dev",
        )
    except Exception as exc:
        assert "--environment must be production or staging" in str(exc)
    else:
        raise AssertionError("Expected invalid environment error")


def test_ansible_update_vms_cli_forwards_flags(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        "cli.ansible_commands.resolve_vault_password",
        lambda **kwargs: "resolved-secret",
    )

    def fake_run_update_vms(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr("cli.ansible_commands.run_update_vms", fake_run_update_vms)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ansible",
            "update-vms",
            "--vault-password",
            "secret",
            "--environment",
            "production",
            "--working-directory",
            "/tmp/project",
            "--playbook",
            "custom-update.yml",
            "--limit",
            "workers",
            "--reboot",
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
        "playbook": "custom-update.yml",
        "limit": "workers",
        "reboot": True,
        "shared_dir": ".roles",
        "version": "stable",
        "refresh": False,
        "repo_url": "https://github.com/example/deploy-your-startup",
    }
