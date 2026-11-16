"""GIVEN moving remotes or CI exports, WHEN setup runs, THEN reviewed role pins hold."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


def run_cli(project, remote, env, *args):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "setup_ansible",
            "--working-directory",
            str(project),
            "--repo-url",
            remote,
            *args,
        ],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def git(root, env, *args):
    return subprocess.run(
        ["git", *args], cwd=root, env=env, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def pinned_project(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    source = tmp_path / "source"
    (source / "roles" / "sample" / "tasks").mkdir(parents=True)
    role = source / "roles" / "sample" / "tasks" / "main.yml"
    role.write_text("- ansible.builtin.debug:\n    msg: reviewed\n")
    git(source, env, "init", "-b", "main")
    git(source, env, "add", ".")
    git(source, env, "commit", "-m", "reviewed roles")
    revision = git(source, env, "rev-parse", "HEAD")
    project = tmp_path / "project"
    project.mkdir()
    remote = source.as_uri()
    result = run_cli(project, remote, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "shared-roles.ref").read_text().strip() == revision
    return project, source, remote, env


def test_moving_main_does_not_move_project_roles(pinned_project):
    # GIVEN the first setup persisted an immutable ref and content checksum.
    project, source, remote, env = pinned_project
    role = source / "roles/sample/tasks/main.yml"
    role.write_text("- ansible.builtin.debug:\n    msg: unreviewed-new-main\n")
    git(source, env, "add", ".")
    git(source, env, "commit", "-m", "moving main")
    # WHEN main advances and the real CLI setup runs again with defaults.
    result = run_cli(project, remote, env)
    # THEN it still runs the originally reviewed role commit.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "reviewed\n"
        in (project / ".shared-roles/roles/sample/tasks/main.yml").read_text()
    )
    assert (
        "unreviewed"
        not in (project / ".shared-roles/roles/sample/tasks/main.yml").read_text()
    )


@pytest.mark.parametrize("modified", [False, True])
def test_ci_exports_must_match_the_pinned_contents(pinned_project, modified):
    # GIVEN an exported role tree without Git metadata, as in a reusable action.
    project, _source, remote, env = pinned_project
    shutil.rmtree(project / ".shared-roles/.git")
    if modified:
        (project / ".shared-roles/roles/sample/tasks/main.yml").write_text(
            "older or modified roles"
        )
    # WHEN CI uses --no-refresh, THEN only the exact reviewed export is accepted.
    result = run_cli(project, remote, env, "--no-refresh")
    assert (result.returncode == 0) is not modified, result.stdout + result.stderr
    if modified:
        assert "Refusing to run" in result.stderr


def test_explicit_pin_updates_are_reviewable_without_deploying(pinned_project):
    # GIVEN a pinned project and a new remote commit.
    project, source, remote, env = pinned_project
    (source / "roles/sample/tasks/main.yml").write_text(
        "- ansible.builtin.debug: {msg: next}\n"
    )
    git(source, env, "add", ".")
    git(source, env, "commit", "-m", "next reviewed roles")
    expected = git(source, env, "rev-parse", "HEAD")
    # WHEN the real CLI pins main, THEN only the two reviewed pin files are updated.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "ansible",
            "pin",
            "--working-directory",
            str(project),
            "--repo-url",
            remote,
        ],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "shared-roles.ref").read_text().strip() == expected
    assert not list(project.glob(".startup-pin-*"))
    result = run_cli(project, remote, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "next" in (project / ".shared-roles/roles/sample/tasks/main.yml").read_text()


def test_pin_moves_shared_workflow_references_with_the_roles(pinned_project):
    # GIVEN a project whose workflows call the owner copy at main and at an older commit.
    project, source, remote, env = pinned_project
    git(source, env, "tag", "v1.2.0")
    expected = git(source, env, "rev-parse", "HEAD")
    workflows = project.parent / ".github/workflows"
    workflows.mkdir(parents=True)
    shared = "sample/deploy-your-startup/.github/workflows"
    (workflows / "deploy.yml").write_text(
        f"jobs:\n  a:\n    uses: {shared}/deploy.yml@main\n"
        f"  b:\n    uses: {shared}/build-and-deploy-service.yml@{'0' * 40} # v1.0.0\n"
        "  c:\n    uses: other/deploy-your-startup/.github/workflows/deploy.yml@main\n"
        "    steps:\n      - uses: actions/checkout@v7\n"
    )
    # WHEN the owner adopts a release for this project.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "ansible", "pin", "--working-directory",
         str(project), "--repo-url", remote, "--version", "v1.2.0"],
        cwd=project, env={**env, "STARTUP_ANSIBLE_REPO_OWNER": "sample"},
        text=True, capture_output=True, check=False, timeout=60,
    )  # fmt: skip
    # THEN roles and shared workflows point at the same immutable commit, and nothing else moves.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "shared-roles.ref").read_text().strip() == expected
    assert (workflows / "deploy.yml").read_text() == (
        f"jobs:\n  a:\n    uses: {shared}/deploy.yml@{expected} # v1.2.0\n"
        f"  b:\n    uses: {shared}/build-and-deploy-service.yml@{expected} # v1.2.0\n"
        "  c:\n    uses: other/deploy-your-startup/.github/workflows/deploy.yml@main\n"
        "    steps:\n      - uses: actions/checkout@v7\n"
    )
    assert "Pinned 2 shared workflow reference(s)" in result.stdout


def test_roles_only_pin_leaves_workflow_references_alone(pinned_project):
    # GIVEN a project that keeps its workflow references on a separate revision.
    project, _source, remote, env = pinned_project
    workflows = project.parent / ".github/workflows"
    workflows.mkdir(parents=True)
    original = "jobs:\n  a:\n    uses: sample/deploy-your-startup/.github/workflows/deploy.yml@main\n"
    (workflows / "deploy.yml").write_text(original)
    # WHEN only the roles are pinned.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "ansible", "pin", "--working-directory",
         str(project), "--repo-url", remote, "--roles-only"],
        cwd=project, env={**env, "STARTUP_ANSIBLE_REPO_OWNER": "sample"},
        text=True, capture_output=True, check=False, timeout=60,
    )  # fmt: skip
    # THEN the pin files are written and the workflows stay as they were.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workflows / "deploy.yml").read_text() == original
    assert "workflow reference" not in result.stdout
