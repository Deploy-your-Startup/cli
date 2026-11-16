"""GIVEN releases of a shared repository, WHEN projects update or migrate, THEN pins move only as asked."""

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"
DEPLOY = "Deploy-your-Startup/deploy"
LEGACY = "sample/deploy-your-startup"


def git(*args, cwd, env):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True
    ).stdout.strip()


@dataclass
class Shared:
    """A bare shared repository whose releases are annotated vX.Y.Z tags."""

    root: Path
    work: Path
    remote: Path
    env: dict

    def release(self, roles: str, version: str) -> str:
        (self.work / "roles/sample.yml").write_text(roles)
        git("add", ".", cwd=self.work, env=self.env)
        git("commit", "-m", roles, cwd=self.work, env=self.env)
        git("tag", "-a", "-m", version, version, cwd=self.work, env=self.env)
        git("push", "-q", "origin", "main", version, cwd=self.work, env=self.env)
        return git("rev-parse", version + "^{commit}", cwd=self.work, env=self.env)

    def project(self, repository: str) -> tuple[Path, Path]:
        project = self.root / "app"
        workflow = project / ".github/workflows/deploy.yml"
        (project / "deployment").mkdir(parents=True)
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "jobs:\n  deploy:\n"
            f"    uses: {repository}/.github/workflows/deploy.yml@main\n"
            "  other:\n    uses: someone/else/.github/workflows/x.yml@v1\n"
        )
        return project, workflow

    def cli(self, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "cli.startup", *args],
            cwd=project, env=self.env, text=True, capture_output=True, check=False, timeout=120,
        )  # fmt: skip


@pytest.fixture
def shared(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    for name in ("GITHUB_ACTIONS", "CI", "GITHUB_TOKEN", "STARTUP_SHARED_REPOSITORY"):
        env.pop(name, None)
    remote, work = tmp_path / "shared.git", tmp_path / "shared"
    git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path, env=env)
    git("clone", "-q", str(remote), str(work), cwd=tmp_path, env=env)
    (work / "roles").mkdir()
    env["STARTUP_ANSIBLE_REPO_URL"] = remote.as_uri()
    return Shared(tmp_path, work, remote, env)


def test_update_moves_a_project_between_releases_and_back(shared):
    # GIVEN a project calling the shared workflows at main, and one published release.
    project, workflow = shared.project(DEPLOY)
    deployment = project / "deployment"
    first = shared.release("reviewed roles", "v1.0.0")
    # WHEN the owner checks, THEN the release is named and nothing changes.
    result = shared.cli(project, "update", "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Update available: release v1.0.0 of {DEPLOY}." in result.stdout
    assert not (deployment / "shared-roles.ref").exists()
    # WHEN the project is updated.
    result = shared.cli(project, "update")
    # THEN roles and shared workflows name the release commit; other references stay.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == first
    assert (
        f"uses: {DEPLOY}/.github/workflows/deploy.yml@{first} # v1.0.0"
        in workflow.read_text()
    )
    assert "someone/else/.github/workflows/x.yml@v1" in workflow.read_text()
    assert (
        "Up to date: this project runs release v1.0.0."
        in shared.cli(project, "update", "--check").stdout
    )
    # WHEN a newer release appears, it is adopted; THEN the earlier one can be restored by name.
    second = shared.release("future roles", "v1.1.0")
    result = shared.cli(project, "update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == second
    assert "role contents changed" in result.stdout
    result = shared.cli(project, "update", "--version", "v1.0.0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == first
    # WHEN a version that was never released is requested, THEN it is refused and nothing moves.
    result = shared.cli(project, "update", "--version", "v9.0.0")
    assert result.returncode != 0
    assert f"{DEPLOY} has no release v9.0.0" in result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == first


def test_update_latest_unpins_a_project_until_it_is_pinned_again(shared):
    # GIVEN a project pinned to a release.
    project, workflow = shared.project(DEPLOY)
    deployment = project / "deployment"
    first = shared.release("reviewed roles", "v1.0.0")
    assert shared.cli(project, "update").returncode == 0
    pinned = workflow.read_text()
    # WHEN the owner previews and then chooses to follow latest.
    assert (
        "Would remove this project's pins"
        in shared.cli(project, "update", "--version", "latest", "--check").stdout
    )
    assert workflow.read_text() == pinned
    result = shared.cli(project, "update", "--version", "latest")
    # THEN the pins are gone, the workflows call main, and the choice is recorded.
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"uses: {DEPLOY}/.github/workflows/deploy.yml@main\n" in workflow.read_text()
    assert not (deployment / "shared-roles.ref").exists()
    assert (deployment / "shared-roles.latest").exists()
    # AND neither a check nor a plain update pins it again, even after a new release.
    shared.release("future roles", "v1.1.0")
    for arguments in (["--check"], []):
        result = shared.cli(project, "update", *arguments)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "This project follows latest" in result.stdout
        assert not (deployment / "shared-roles.ref").exists()
    # WHEN the owner names a release, THEN the project is pinned again and stops following.
    result = shared.cli(project, "update", "--version", "v1.0.0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == first
    assert workflow.read_text() == pinned
    assert not (deployment / "shared-roles.latest").exists()


def test_an_owner_copy_project_migrates_once_to_the_current_release(shared):
    # GIVEN a project on an owner copy with an old pin, and two releases of the new source.
    project, workflow = shared.project(LEGACY)
    deployment = project / "deployment"
    (deployment / "shared-roles.ref").write_text("0" * 40 + "\n")
    (deployment / "shared-roles.sha256").write_text("old\n")
    shared.release("earlier roles", "v1.0.0")
    current = shared.release("current roles", "v1.1.0")
    original = workflow.read_text()
    # WHEN a plain update runs, THEN it points the owner at the migration instead.
    result = shared.cli(project, "update")
    assert result.returncode != 0
    assert "startup migrate deploy" in result.stderr
    # WHEN the migration is previewed, THEN it names the move and changes nothing.
    result = shared.cli(project, "migrate", "deploy", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"from {LEGACY} to {DEPLOY} and pin release v1.1.0" in result.stdout
    assert workflow.read_text() == original
    assert (deployment / "shared-roles.ref").read_text().strip() == "0" * 40
    # WHEN it runs.
    result = shared.cli(project, "migrate", "deploy")
    # THEN workflows and roles name the current release of the new source, and the copy may be archived.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == current
    assert (
        f"uses: {DEPLOY}/.github/workflows/deploy.yml@{current} # v1.1.0"
        in workflow.read_text()
    )
    assert LEGACY not in workflow.read_text()
    assert "someone/else/.github/workflows/x.yml@v1" in workflow.read_text()
    assert not (deployment / "shared-roles.source").exists()
    assert f"gh repo archive {LEGACY}" in result.stdout
    # AND the role checkout does not leak Git's detached-HEAD advice or fetch progress.
    assert "detached HEAD" not in result.stdout + result.stderr
    assert "FETCH_HEAD" not in result.stdout + result.stderr
    # AND a second run has nothing to do, while update now works against the new source.
    assert "nothing to migrate" in shared.cli(project, "migrate", "deploy").stdout
    assert (
        "Up to date: this project runs release v1.1.0."
        in shared.cli(project, "update", "--check").stdout
    )


def test_a_project_can_move_to_a_fork_and_back(shared):
    # GIVEN a project on the default source and a release in the fork.
    project, workflow = shared.project(DEPLOY)
    deployment = project / "deployment"
    release = shared.release("fork roles", "v2.0.0")
    # WHEN it migrates to the fork.
    result = shared.cli(
        project, "migrate", "deploy", "--source", "example-owner/deploy"
    )
    # THEN the fork is recorded and pinned, and updates follow the fork.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        deployment / "shared-roles.source"
    ).read_text().strip() == "example-owner/deploy"
    assert (
        f"uses: example-owner/deploy/.github/workflows/deploy.yml@{release} # v2.0.0"
        in workflow.read_text()
    )
    assert (
        "Up to date: this project runs release v2.0.0."
        in shared.cli(project, "update", "--check").stdout
    )
    # WHEN it migrates back to the default source, THEN the record is removed.
    result = shared.cli(project, "migrate", "deploy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (deployment / "shared-roles.source").exists()
    assert (
        f"uses: {DEPLOY}/.github/workflows/deploy.yml@{release} # v2.0.0"
        in workflow.read_text()
    )
