"""GIVEN real Git releases, WHEN adopting one version, THEN local and CI agree."""

import shutil

import pytest
import yaml

from tests import test_shared_source_integration as scenarios

DEPLOY = scenarios.DEPLOY


@pytest.fixture
def shared(tmp_path):
    return scenarios.make_shared(tmp_path)


ROUTER = """name: Project
on:
  workflow_call:
    inputs:
      operation: {type: string, required: true}
      service: {type: string, default: ''}
    secrets:
      VAULT_PASSWORD: {required: true}
jobs:
  build:
    uses: ./.github/workflows/build-and-deploy-service.yml
"""


def publish_router(shared, pages=False):
    directory = shared.work / ".github/workflows"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ("project-pages.yml" if pages else "project.yml")).write_text(ROUTER)
    return shared.release("new runtime", "v2.0.0")


def test_migration_keeps_jobs_and_records_only_one_version(shared):
    # GIVEN legacy direct calls, job dependencies, unrelated workflows and old pins.
    project, workflow = shared.project(DEPLOY)
    workflow.write_text(
        "on:\n  push:\n    paths: ['backend/**']\n  pull_request:\n    paths:\n      - backend/**\n"
        "  pull_request_target:\n    paths:\n      [\n        'shared/**',\n      ]\n"
        + workflow.read_text()
    )
    shared.release("old runtime", "v1.0.0")
    assert shared.cli(project, "update").returncode == 0
    expected = publish_router(shared)
    # WHEN the owner adopts the new release through the real CLI.
    result = shared.cli(project, "update")
    # THEN existing jobs, settings and dependencies survive; only the entry names a version.
    assert result.returncode == 0, result.stdout + result.stderr
    entry = project / ".github/workflows/startup.yml"
    assert entry.read_text().count(f"{DEPLOY}/.github/") == 1
    assert "@" + expected in entry.read_text()
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    triggers = yaml.safe_load(workflow.read_text())[True]
    assert all(
        ".github/workflows/startup.yml" in event["paths"] for event in triggers.values()
    )
    assert jobs["build"]["needs"] == "deploy"
    assert jobs["build"]["with"] == {"service": "backend", "operation": "build"}
    assert jobs["deploy"]["with"] == {"operation": "deploy"}
    assert jobs["other"]["uses"].startswith("someone/else/")
    assert DEPLOY not in workflow.read_text()
    assert not list((project / "deployment").glob("shared-roles.*"))
    assert "Nothing is deployed" in result.stdout
    assert shared.cli(project, "update", "--check").returncode == 0
    before = entry.read_text(), workflow.read_text()
    assert shared.cli(project, "update").returncode == 0
    assert (entry.read_text(), workflow.read_text()) == before
    # WHEN a newer release is selected, THEN it changes just the single external ref.
    next_revision = shared.release("next runtime", "v2.1.0")
    assert shared.cli(project, "update").returncode == 0
    assert "@" + next_revision in entry.read_text()
    assert workflow.read_text() == before[1]
    # WHEN explicitly rolling back to an old release, THEN its legacy interface still works.
    result = shared.cli(project, "update", "--version", "v1.0.0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not entry.exists()
    assert (project / "deployment/shared-roles.ref").exists()
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    assert "/build-and-deploy-service.yml@" in jobs["build"]["uses"]
    assert "operation" not in jobs["build"]["with"]


@pytest.mark.parametrize("modified", [False, True])
def test_exports_are_compared_to_the_one_configured_commit(shared, modified):
    # GIVEN one configured deployment version and an action export without Git metadata.
    project, _ = shared.project(DEPLOY)
    publish_router(shared)
    assert shared.cli(project, "update").returncode == 0
    directory = project / "deployment"
    result = shared.cli(
        project, "ansible", "setup_ansible", "--working-directory", str(directory)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.rmtree(directory / ".shared-roles/.git")
    if modified:
        (directory / ".shared-roles/roles/sample.yml").write_text("tampered export")
    # WHEN CI reuses that export, THEN modified bytes are refused before Ansible runs.
    result = shared.cli(
        project,
        "ansible",
        "setup_ansible",
        "--working-directory",
        str(directory),
        "--no-refresh",
    )
    assert (result.returncode == 0) is not modified, result.stdout + result.stderr
    if modified:
        assert "Refusing to run exported roles" in result.stderr
    assert not (directory / "shared-roles.ref").exists()
    assert not (directory / "shared-roles.sha256").exists()


def test_unsupported_calls_leave_all_files_unchanged(shared):
    # GIVEN a custom shared workflow outside the router's public operations.
    project, workflow = shared.project(DEPLOY)
    workflow.write_text(workflow.read_text().replace("/deploy.yml@", "/custom.yml@"))
    publish_router(shared)
    before = workflow.read_text()
    # WHEN migrating, THEN the CLI refuses a partial rewrite.
    result = shared.cli(project, "update")
    assert result.returncode != 0
    assert "Unsupported shared workflow" in result.stderr
    assert workflow.read_text() == before
    assert not (project / ".github/workflows/startup.yml").exists()
    assert not (project / "deployment/shared-roles.ref").exists()


@pytest.mark.parametrize("target", ["main", "v1.0.0"])
def test_single_version_refuses_role_only_updates_and_conflicting_pins(shared, target):
    # GIVEN a project using the single-version release.
    project, workflow = shared.project(DEPLOY)
    shared.release("older runtime", "v1.0.0")
    revision = publish_router(shared)
    assert shared.cli(project, "update").returncode == 0
    before = workflow.read_text()
    # WHEN splitting local roles from CI is requested, THEN no files change.
    result = shared.cli(
        project,
        "ansible",
        "pin",
        "--working-directory",
        str(project / "deployment"),
        "--roles-only",
        "--version",
        target,
    )
    assert result.returncode != 0
    assert "--roles-only cannot split" in result.stderr
    assert workflow.read_text() == before
    # WHEN a conflicting old pin is introduced, THEN setup cannot silently choose one.
    (project / "deployment/shared-roles.ref").write_text("0" * 40)
    result = shared.cli(
        project,
        "ansible",
        "setup_ansible",
        "--working-directory",
        str(project / "deployment"),
    )
    assert result.returncode != 0
    assert "conflicts" in result.stderr
    assert revision in (project / ".github/workflows/startup.yml").read_text()


def test_pages_uses_the_same_single_version_without_creating_ansible_files(shared):
    # GIVEN a workflow-only Pages project.
    project, workflow = shared.project(DEPLOY)
    (project / "deployment").rmdir()
    workflow.write_text(
        f"jobs:\n  publish:\n    uses: {DEPLOY}/.github/workflows/deploy-pages.yml@main\n    with:\n      project_name: example\n"
    )
    revision = publish_router(shared, pages=True)
    # WHEN updating, THEN the one native entry supplies the Pages workflow too.
    result = shared.cli(project, "update")
    assert result.returncode == 0, result.stdout + result.stderr
    entry = project / ".github/workflows/startup.yml"
    assert f"project-pages.yml@{revision}" in entry.read_text()
    assert "operation: pages" in workflow.read_text()
    assert not (project / "deployment").exists()
    assert not list((project / ".github").glob("shared-roles.*"))
    assert shared.cli(project, "update", "--check").returncode == 0


def test_comments_are_not_deployment_configuration(shared):
    # GIVEN a migrated project with historical references mentioned in comments.
    project, workflow = shared.project(DEPLOY)
    publish_router(shared)
    assert shared.cli(project, "update").returncode == 0
    entry = project / ".github/workflows/startup.yml"
    entry.write_text(
        entry.read_text() + f"\n# uses: {DEPLOY}/.github/workflows/project.yml@old\n"
    )
    workflow.write_text(
        workflow.read_text() + f"\n# uses: {DEPLOY}/.github/workflows/deploy.yml@old\n"
    )
    # WHEN local roles are prepared, THEN only the actual native uses field selects the version.
    result = shared.cli(
        project,
        "ansible",
        "setup_ansible",
        "--working-directory",
        str(project / "deployment"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # WHEN the only real reference is commented out, THEN the CLI cannot fall back to main.
    status = shared.cli(project, "update", "--check")
    assert status.returncode == 0, status.stdout + status.stderr
    assert "Up to date" in status.stdout
    entry.write_text(
        "\n".join("# " + line for line in entry.read_text().splitlines()) + "\n"
    )
    result = shared.cli(
        project,
        "ansible",
        "setup_ansible",
        "--working-directory",
        str(project / "deployment"),
    )
    assert result.returncode != 0
    assert "must contain one" in result.stderr


@pytest.mark.parametrize("compatible", [False, True])
def test_migration_checks_the_actual_project_locked_cli(shared, compatible):
    # GIVEN a global candidate CLI but a separately locked deployment runtime.
    project, workflow = shared.project(DEPLOY)
    expected = publish_router(shared)
    deployment = project / "deployment"
    import subprocess
    from pathlib import Path

    cli_source = Path(__file__).resolve().parents[1]
    source = (
        cli_source.as_uri()
        if compatible
        else "https://github.com/Deploy-your-Startup/cli.git"
    )
    revision = (
        scenarios.git("rev-parse", "HEAD", cwd=cli_source, env=shared.env)
        if compatible
        else "20c3efa9031d1510812093273b74cd7e756704e0"
    )
    (deployment / "pyproject.toml").write_text(
        '[project]\nname="deployment"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
        'dependencies=["deploy-your-startup-cli"]\n[tool.uv]\npackage=false\n'
        "override-dependencies=[\"playwright; sys_platform == 'never'\"]\n"
        f'[tool.uv.sources]\ndeploy-your-startup-cli={{git="{source}",rev="{revision}"}}\n'
    )
    locked = subprocess.run(
        ["uv", "lock", "--project", str(deployment)],
        env=shared.env,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    assert locked.returncode == 0, locked.stdout + locked.stderr
    original = workflow.read_text()
    # WHEN adopting the one-version release with the real CLI.
    result = shared.cli(project, "update")
    # THEN only a project whose actual locked runtime supports the format is migrated.
    assert (result.returncode == 0) is compatible, result.stdout + result.stderr
    if compatible:
        assert expected in (project / ".github/workflows/startup.yml").read_text()
        assert not (deployment / "shared-roles.ref").exists()
    else:
        assert "project's locked Startup CLI" in result.stderr
        assert workflow.read_text() == original
        assert not (project / ".github/workflows/startup.yml").exists()
