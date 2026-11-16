"""GIVEN an owner copy, WHEN the template is synced, THEN each sync is an immutable release."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"
WORKFLOW = ".github/workflows/deploy.yml"
ACTION = ".github/actions/deploy/action.yml"
OWNER_COPY = (
    "§§deploy_your_startup.github_username§§/§§deploy_your_startup.deploy_repo_name§§"
)
FAKE_GH = """import os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['repo', 'clone']:
    remote = Path(os.environ['TEST_REMOTES']) / (args[2].split('/')[-1] + '.git')
    extra = args[5:] if len(args) > 4 else []
    raise SystemExit(subprocess.call(['git', 'clone', *extra, str(remote), args[3]]))
if args[:1] == ['api'] and args[1].startswith('users/'):
    print('User')
"""


def git(*args, cwd, env):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True
    ).stdout.strip()


class Remotes:
    """A template and an owner copy as bare repositories behind a fake ``gh``."""

    def __init__(self, root):
        self.root = root
        self.env = {
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "GIT_AUTHOR_NAME": "Integration",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Integration",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "TEST_REMOTES": str(root),
        }
        self.template = root / "template"
        self.target = root / "deploy-your-startup.git"
        for name in ["template", "owner"]:
            checkout = root / name
            checkout.mkdir()
            git("init", "-b", "main", cwd=checkout, env=self.env)
            (checkout / "README.md").write_text("initial\n")
            git("add", ".", cwd=checkout, env=self.env)
            git("commit", "-m", "initial", cwd=checkout, env=self.env)
        git(
            "init",
            "--bare",
            "-b",
            "main",
            str(root / "deploy-template.git"),
            cwd=root,
            env=self.env,
        )
        git(
            "remote",
            "add",
            "origin",
            str(root / "deploy-template.git"),
            cwd=self.template,
            env=self.env,
        )
        git(
            "clone",
            "--bare",
            str(root / "owner"),
            str(self.target),
            cwd=root,
            env=self.env,
        )
        fake_bin = root / "bin"
        fake_bin.mkdir()
        (fake_bin / "gh").write_text("#!" + sys.executable + "\n" + FAKE_GH)
        (fake_bin / "gh").chmod(0o755)
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env["PATH"]

    def release(self, role, version=None):
        """Commit a template revision whose workflow and action call the owner copy's main."""
        for path, body in {
            WORKFLOW: f"jobs:\n  deploy:\n    steps:\n      - uses: {OWNER_COPY}/.github/actions/deploy@main\n",
            ACTION: f"runs:\n  steps:\n    - uses: {OWNER_COPY}/.github/actions/join-tailnet@main\n"
            "    - uses: actions/checkout@main\n",
            "roles/sample.yml": role + "\n",
        }.items():
            (self.template / path).parent.mkdir(parents=True, exist_ok=True)
            (self.template / path).write_text(body)
        git("add", ".", cwd=self.template, env=self.env)
        git("commit", "-m", role, cwd=self.template, env=self.env)
        if version:
            git("tag", version, cwd=self.template, env=self.env)
        git("push", "--tags", "origin", "main", cwd=self.template, env=self.env)
        return git("rev-parse", "--short=12", "HEAD", cwd=self.template, env=self.env)

    def sync(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "cli.startup", "sync", "--owner", "sample", *args],
            cwd=self.root, env=self.env, text=True, capture_output=True, check=False, timeout=60,
        )  # fmt: skip

    def show(self, ref, path):
        return git("show", f"{ref}:{path}", cwd=self.target, env=self.env)

    def commit(self, ref):
        return git("rev-parse", f"{ref}^{{commit}}", cwd=self.target, env=self.env)


@pytest.fixture
def remotes(tmp_path):
    return Remotes(tmp_path)


def test_a_later_sync_does_not_change_an_earlier_release(remotes):
    # GIVEN a first synced release that a project has pinned.
    first = "source-" + remotes.release("reviewed roles")
    result = remotes.sync()
    assert result.returncode == 0, result.stdout + result.stderr
    pinned = remotes.commit(first)
    assert pinned == remotes.commit("main")
    # WHEN the template moves on and the owner syncs again.
    second = "source-" + remotes.release("future roles")
    result = remotes.sync()
    assert result.returncode == 0, result.stdout + result.stderr
    # THEN the pinned release still resolves its own workflows, actions and roles,
    assert remotes.commit(first) == pinned
    assert f"sample/deploy-your-startup/.github/actions/deploy@{first}" in remotes.show(
        pinned, WORKFLOW
    )
    assert (
        f"sample/deploy-your-startup/.github/actions/join-tailnet@{first}"
        in remotes.show(pinned, ACTION)
    )
    assert remotes.show(pinned, "roles/sample.yml") == "reviewed roles"
    # AND main carries the new, equally self-contained release; foreign actions stay untouched.
    assert remotes.commit(second) == remotes.commit("main") != pinned
    assert f"/.github/actions/deploy@{second}" in remotes.show("main", WORKFLOW)
    assert "actions/checkout@main" in remotes.show("main", ACTION)
    assert f"startup update --version {second}" in result.stdout


def test_tag_only_publishes_a_release_without_moving_main(remotes):
    # GIVEN an owner copy whose main serves unpinned projects.
    before = remotes.commit("main")
    remotes.release("candidate roles", version="v1.2.0")
    # WHEN a tagged template version is published as a release only.
    result = remotes.sync("--source-version", "v1.2.0", "--tag-only")
    # THEN the release is named after the version and main stays where it was.
    assert result.returncode == 0, result.stdout + result.stderr
    assert remotes.commit("main") == before
    assert "/.github/actions/deploy@v1.2.0" in remotes.show("v1.2.0", WORKFLOW)
    # AND repeating either kind of sync never moves the published release.
    release = remotes.commit("v1.2.0")
    assert (
        "Already up to date"
        in remotes.sync("--source-version", "v1.2.0", "--tag-only").stdout
    )
    assert remotes.sync("--source-version", "v1.2.0").returncode == 0
    assert remotes.commit("v1.2.0") == release == remotes.commit("main")
    assert remotes.show("main", "roles/sample.yml") == "candidate roles"


def test_sync_refuses_to_replace_a_published_release(remotes):
    # GIVEN a published release whose owner later renames the shared repository.
    remotes.release("reviewed roles", version="v1.0.0")
    assert remotes.sync("--source-version", "v1.0.0").returncode == 0
    release = remotes.commit("v1.0.0")
    (remotes.root / "other.git").symlink_to(remotes.target)
    # WHEN the same version would render different contents.
    result = remotes.sync("--source-version", "v1.0.0", "--repo-name", "other")
    # THEN the release is kept and the sync fails without moving main.
    assert result.returncode != 0
    assert "immutable" in result.stderr
    assert remotes.commit("v1.0.0") == release == remotes.commit("main")


def test_dry_run_names_the_release_without_publishing(remotes):
    # GIVEN a new template revision.
    tag = "source-" + remotes.release("reviewed roles")
    before = remotes.commit("main")
    # WHEN the sync is previewed.
    result = remotes.sync("--dry-run")
    # THEN it names the release and leaves the owner copy untouched.
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Would publish release {tag}." in result.stdout
    assert remotes.commit("main") == before
    assert git("tag", cwd=remotes.target, env=remotes.env) == ""
