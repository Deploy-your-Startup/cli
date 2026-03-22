import json
import os
from typing import Any

import click
import pytest
from click.testing import CliRunner

from cli import ansible_commands
from cli.startup import cli


def test_run_restore_uses_latest_backup_files(tmp_path, monkeypatch):
    project_root = tmp_path / "demo-app"
    working_dir = project_root / "deployment"
    shared_dir = working_dir / ".shared-roles"
    shared_dir.mkdir(parents=True)
    (shared_dir / "restore-playbook.yml").write_text("---\n", encoding="utf-8")

    backup_root = tmp_path / "Backups" / "demo-app"
    old_dir = backup_root / "2026-03-21_10-00-00"
    new_dir = backup_root / "2026-03-22_10-00-00"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)

    old_db = old_dir / "demo-app-db-production-old.sql.gz"
    old_media = old_dir / "demo-app-media-production-old.tar.gz"
    new_db = new_dir / "demo-app-db-production-new.sql.gz"
    new_media = new_dir / "demo-app-media-production-new.tar.gz"

    for path in (old_db, old_media, new_db, new_media):
        path.write_text("data", encoding="utf-8")

    os.utime(old_db, (1, 1))
    os.utime(old_media, (1, 1))
    os.utime(new_db, (2, 2))
    os.utime(new_media, (2, 2))

    captured: dict[str, Any] = {}

    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: shared_dir)
    monkeypatch.setattr(ansible_commands, "get_hcloud_token", lambda *args: "token")
    monkeypatch.setattr(ansible_commands, "_find_uv", lambda: "uv")
    monkeypatch.setattr(ansible_commands, "_ansible_env", lambda *args: {})

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(ansible_commands, "_run_command", fake_run)

    ansible_commands.run_restore(
        vault_password="secret",
        environment="production",
        working_directory=str(working_dir),
        backup_dir=str(backup_root),
        confirm=True,
    )

    extra_vars = json.loads(captured["command"][-1])
    assert extra_vars["db_backup_file"] == str(new_db.resolve())
    assert extra_vars["media_backup_file"] == str(new_media.resolve())
    assert extra_vars["restore_db"] is True
    assert extra_vars["restore_media"] is True
    assert captured["kwargs"]["env"]["HCLOUD_TOKEN"] == "token"


def test_run_restore_requires_confirmation(tmp_path, monkeypatch):
    working_dir = tmp_path / "deployment"
    working_dir.mkdir()
    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: None)

    with pytest.raises(click.ClickException, match="destructive"):
        ansible_commands.run_restore(
            vault_password="secret",
            environment="production",
            working_directory=str(working_dir),
        )


def test_ansible_restore_cli_forwards_flags(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run_restore(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("cli.ansible_commands.run_restore", fake_run_restore)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ansible",
            "restore",
            "--vault-password",
            "secret",
            "--environment",
            "production",
            "--working-directory",
            "deployment",
            "--backup-dir",
            "/tmp/backups",
            "--no-restore-media",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert captured["vault_password"] == "secret"
    assert captured["environment"] == "production"
    assert captured["working_directory"] == "deployment"
    assert captured["backup_dir"] == "/tmp/backups"
    assert captured["restore_db"] is True
    assert captured["restore_media"] is False
    assert captured["confirm"] is True
