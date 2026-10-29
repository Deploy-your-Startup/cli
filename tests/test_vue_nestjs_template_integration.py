"""Exercise the public NestJS template through real rendering and Vault updates."""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from cli.bootstrap import TEMPLATE_VAULT_PASSWORD
from cli.template_commands import VUE_NESTJS_TEMPLATE, VUE_NESTJS_TEMPLATE_VERSION
from tests.test_vault_process_integration import cli
from tests.vault_support import RealVault

ROOT = Path(__file__).resolve().parents[1]


def test_vue_nestjs_template_renders_one_postgres_application_and_accepts_vault_updates(
    tmp_path,
):
    # GIVEN the immutable NestJS template and synthetic, public project settings.
    source = os.environ.get("STARTUP_NESTJS_TEMPLATE_SOURCE", VUE_NESTJS_TEMPLATE)
    project = tmp_path / "example"
    # WHEN bootstrap's real rendering entrypoint inspects and generates it.
    program = """
import json, sys
from pathlib import Path
from cli.template_commands import render_project, template_supports_shared_cluster
values = json.loads(sys.stdin.read())
assert template_supports_shared_cluster(values['source'], values['revision'])
render_project(Path(values['project']), {
    'project_name': 'example', 'base_domain': 'example.invalid',
    'github_username': 'example-owner', 'k8s_namespace': 'example-production',
}, source=values['source'], version=values['revision'])
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(
            {
                "source": source,
                "revision": VUE_NESTJS_TEMPLATE_VERSION,
                "project": str(project),
            }
        ),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # THEN PostgreSQL, namespace, the one application image and E2E gate agree.
    compose = yaml.safe_load((project / "compose.yml").read_text())
    assert compose["services"]["db"]["image"] == "postgres:18.6"
    workflow = yaml.safe_load(
        (project / ".github/workflows/build-and-deploy-backend.yml").read_text()
    )
    settings = workflow["jobs"]["application"]["with"]
    assert settings["service"] == "backend"
    assert settings["run_lint"] and settings["run_tests"] and settings["run_e2e"]
    assert settings["additional_build_context"] == "application=."
    playbook = (project / "deployment/playbook.yml").read_text()
    assert "name: postgres" in playbook and "name: backend" in playbook
    assert "name: frontend" not in playbook
    data_source = (project / "backend/data-source.ts").read_text()
    assert "type: 'postgres'" in data_source and "synchronize: false" in data_source
    deployment = project / "deployment"
    public = yaml.load(
        (deployment / "group_vars/all.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert public["k8s_namespace"] == "example-production"
    # WHEN a generated database secret is updated through the real CLI.
    changed = cli(
        tmp_path,
        "update",
        "-r",
        deployment,
        "--field-stdin",
        "backend_db_password",
        "--verify-password",
        stdin=b"synthetic-nestjs-template-secret",
        password=TEMPLATE_VAULT_PASSWORD,
    )
    assert changed.returncode == 0, changed.stdout + changed.stderr
    values = yaml.load(
        (deployment / "group_vars/production.yml").read_text(), Loader=yaml.BaseLoader
    )
    # THEN the same public template Vault baseline decrypts the updated field.
    assert (
        RealVault(TEMPLATE_VAULT_PASSWORD).decrypt(values["backend_db_password"])
        == b"synthetic-nestjs-template-secret"
    )
