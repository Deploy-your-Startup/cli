"""Regression tests using real CLI processes, Vault crypto and local Ansible."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.vault_support import RealVault

SOURCE = Path(__file__).resolve().parents[1] / "src"
PASSWORD = "integration-vault-password"
VALUE = "integration-content\nwith a second line"


def environment(tmp_path):
    temporary = tmp_path / "temporary"
    temporary.mkdir(exist_ok=True)
    return {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "TMPDIR": str(temporary),
    }


def run_cli(tmp_path, *args, input=None, env=None):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", *args],
        cwd=tmp_path,
        env=env or environment(tmp_path),
        input=input,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def vault(password=PASSWORD):
    return RealVault(password)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("encrypted_yaml", [False, True])
def test_content_update_leaves_no_plaintext_temporary_file(
    tmp_path, dry_run, encrypted_yaml
):
    path = tmp_path / ("secrets.yml" if encrypted_yaml else "deploy_key")
    path.write_bytes(
        vault().encrypt(b"item: before\n" if encrypted_yaml else b"before")
    )
    before = path.read_bytes()
    if encrypted_yaml:
        args = ["--field-stdin", "item"]
    else:
        args = ["--file-content", path.name, VALUE]
    result = run_cli(
        tmp_path,
        "secrets",
        "update",
        "-r",
        str(tmp_path),
        "-p",
        PASSWORD,
        *args,
        *(["--dry-run"] if dry_run else []),
        input=VALUE if encrypted_yaml else None,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list((tmp_path / "temporary").iterdir())
    output = tmp_path / "dry-run-output" / path.name if dry_run else path
    decrypted = vault().decrypt(output.read_bytes()).decode()
    if encrypted_yaml:
        import yaml

        assert yaml.safe_load(decrypted)["item"] == VALUE
    else:
        assert decrypted == VALUE
    if dry_run:
        assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["get-field", "update-inline-field"])
def test_missing_file_returns_failure_to_shell(tmp_path, command):
    args = ["--value", "replacement"] if command == "update-inline-field" else []
    result = run_cli(
        tmp_path,
        "secrets",
        command,
        "-f",
        str(tmp_path / "missing.yml"),
        "--field",
        "item",
        *args,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr


def test_failed_secret_update_returns_failure_and_preserves_file(tmp_path):
    path = tmp_path / "secrets.yml"
    path.write_bytes(vault().encrypt(b"item: unchanged\n"))
    before = path.read_bytes()
    result = run_cli(
        tmp_path,
        "secrets",
        "update",
        "-r",
        str(path),
        "-p",
        "wrong-password",
        "--verify-password",
        "--field-stdin",
        "item",
        input="replacement",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert not list((tmp_path / "temporary").iterdir())
    assert path.read_bytes() == before
    assert vault().decrypt(path.read_bytes()) == b"item: unchanged\n"


def test_missing_field_update_returns_failure(tmp_path):
    path = tmp_path / "secrets.yml"
    path.write_text("project_name: example\n")
    result = run_cli(
        tmp_path,
        "secrets",
        "update",
        "-r",
        str(path),
        "-p",
        PASSWORD,
        "--field-stdin",
        "missing",
        input="replacement",
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list((tmp_path / "temporary").iterdir())
    assert path.read_text() == "project_name: example\n"


@pytest.mark.parametrize("include_successful_file", [False, True])
def test_rotation_write_failure_is_not_success(tmp_path, include_successful_file):
    path = tmp_path / "blocked.yml"
    block = vault().encrypt(VALUE.encode()).decode()
    path.write_text(
        "item: !vault |\n" + "".join("  " + line + "\n" for line in block.splitlines())
    )
    before = path.read_bytes()
    # safe_write cannot open its temporary output because a directory owns it.
    path.with_suffix(".yml.tmp").mkdir()
    successful = tmp_path / "successful.yml"
    if include_successful_file:
        successful.write_bytes(vault().encrypt(b"other content"))
    result = run_cli(
        tmp_path,
        "secrets",
        "rotate-password",
        "-r",
        str(tmp_path),
        "--old-password",
        PASSWORD,
        "--new-password",
        "new-password",
        "--strict",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Error writing" in result.stdout + result.stderr
    assert path.read_bytes() == before
    if include_successful_file:
        assert (
            vault("new-password").decrypt(successful.read_bytes()) == b"other content"
        )


def test_legacy_deploy_configuration_failure_returns_failure(tmp_path):
    env = environment(tmp_path)
    env.pop("GITHUB_CLIENT_ID", None)
    env.pop("GITHUB_CLIENT_SECRET", None)
    result = run_cli(tmp_path, "deploy", "create", "--repo-name", "example", env=env)
    assert result.returncode == 1
    assert "Missing GITHUB_CLIENT_ID" in result.stdout


@pytest.mark.parametrize("fail", [False, True])
def test_deploy_uses_real_ansible_without_exposing_token(tmp_path, fail):
    token = "local-integration-token-only"
    description = "Philipp's project with spaces and = signs"
    # A local playbook exercises real extra-var parsing and the child process's
    # environment/argv. No GitHub or cloud service is contacted.
    playbook = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": [
                {
                    "name": "Check environment, arguments and public parameters",
                    "ansible.builtin.assert": {
                        "that": [
                            "github_token == lookup('ansible.builtin.env', 'REVIEW_EXPECTED_TOKEN')",
                            "github_token not in lookup('ansible.builtin.pipe', 'ps -ww -axo command')",
                            "repo_name == 'example'",
                            "repo_description == lookup('ansible.builtin.env', 'REVIEW_DESCRIPTION')",
                            "repo_private == false",
                        ],
                    },
                    "no_log": True,
                },
                {
                    "name": "Record validation",
                    "ansible.builtin.copy": {
                        "content": "checked",
                        "dest": str(tmp_path / "validated"),
                    },
                },
                *(
                    [
                        {
                            "name": "Intentional failure",
                            "ansible.builtin.fail": {"msg": "local failure"},
                        }
                    ]
                    if fail
                    else []
                ),
            ],
        }
    ]
    (tmp_path / "playbook.yml").write_text(json.dumps(playbook))
    env = environment(tmp_path)
    env.update(
        REVIEW_EXPECTED_TOKEN=token,
        REVIEW_DESCRIPTION=description,
        ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-local"),
    )
    code = """
import os
import sys
from cli.deploy import run_ansible_deploy
ok = run_ansible_deploy(os.environ['REVIEW_EXPECTED_TOKEN'], 'example', os.environ['REVIEW_DESCRIPTION'], False, 'owner', 'template', verbose=True)
sys.exit(0 if ok else 1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert (tmp_path / "validated").read_text() == "checked", (
        result.stdout + result.stderr
    )
    assert result.returncode == (1 if fail else 0), result.stdout + result.stderr
    assert token not in result.stdout + result.stderr
    if fail:
        assert "Ansible deployment failed" in result.stdout
