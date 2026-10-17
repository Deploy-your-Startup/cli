"""Real CLI, Copier and Git lifecycle; no account or infrastructure mutations."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )


def cli(root, *args, answers="", env=None):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", *args],
        cwd=root,
        input=answers,
        text=True,
        capture_output=True,
        check=False,
        timeout=40,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "NO_COLOR": "1",
            **(env or {}),
        },
    )


@pytest.fixture
def workspace(tmp_path):
    # GIVEN a real versioned Copier source with the Factory contract.
    source = tmp_path / "source"
    source.mkdir()
    (source / "copier.yml").write_text("project_name: my-factory\n")
    (source / ".startup-factory.yml").write_text("schema: 1\n")
    (source / "projects.yaml").write_text(
        "# Owner registry\nprojects: []\n\n# Shared tools\ntooling: []\n"
    )
    (source / ".copier-answers.yml.jinja").write_text(
        "{{ _copier_answers | to_nice_yaml }}\n"
    )
    (source / "README.md.jinja").write_text("# {{ project_name }}\n")
    git(source, "init", "-b", "main")
    git(
        source,
        "-c",
        "user.name=Factory tests",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "add",
        "-A",
    )
    git(
        source,
        "-c",
        "user.name=Factory tests",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "template",
    )
    git(source, "-c", "tag.gpgsign=false", "tag", "v1.0.0")
    destination = tmp_path / "owner's factory"
    return source, destination


def initialize(workspace, *options, env=None):
    source, destination = workspace
    return cli(
        source.parent,
        "factory",
        "init",
        "--template",
        str(source),
        "--version",
        "v1.0.0",
        "--project-dir",
        str(destination),
        *options,
        env=env,
    )


def project_in(
    destination,
    name="example-app",
    repo="https://github.com/example-owner/example-app.git",
):
    project = destination / "projects/startups" / name
    project.mkdir(parents=True)
    git(project, "init", "-b", "main")
    git(project, "remote", "add", "origin", repo)
    return project


def test_creation_and_resume_preserve_owner_files(workspace):
    # WHEN the real CLI creates a Factory and the owner adds data before resuming.
    first = initialize(workspace)
    assert first.returncode == 0, first.stdout + first.stderr
    _source, destination = workspace
    registry = destination / "projects.yaml"
    registry.write_text("# Personal comment\nprojects: []\ntooling: []\n")
    (destination / "private-notes.md").write_text("owner notes")
    before = {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()}
    second = initialize(workspace)
    # THEN no owner files or the versioned baseline are rewritten.
    assert second.returncode == 0, second.stderr
    assert {
        p.name: p.read_bytes() for p in destination.iterdir() if p.is_file()
    } == before
    assert (
        yaml.safe_load((destination / ".copier-answers.yml").read_text())["_commit"]
        == "v1.0.0"
    )
    assert "Sprowi is optional" in second.stdout
    assert str(destination / "projects/startups") not in first.stderr


def test_setup_without_kubectl_prepares_workspace_without_installing_it(
    workspace, tmp_path
):
    # GIVEN every required Factory tool except kubectl, and Homebrew available.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "calls"
    for tool in ("mise", "uv", "yq", "gh", "brew"):
        (binaries / tool).write_text(
            f'#!/bin/sh\nprintf \'{tool} %s\\n\' "$*" >> "{calls}"\n'
        )
        (binaries / tool).chmod(0o755)
    path = os.pathsep.join((str(binaries), "/usr/bin", "/bin"))
    assert shutil.which("kubectl", path=path) is None
    # WHEN the real CLI creates the Factory with setup.
    result = initialize(workspace, "--setup", env={"PATH": path})
    # THEN setup completes, runs the workspace init and never installs kubectl.
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "mise exec -- ./scripts/init.sh" in recorded
    assert "brew" not in recorded
    assert "Missing Factory tools" not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["nonempty", "symlink", "foreign-template"])
def test_refuses_unrelated_destinations(workspace, kind):
    # GIVEN an owner folder that must not be adopted or overwritten.
    _source, destination = workspace
    destination.mkdir()
    (destination / "notes").write_text("keep")
    if kind == "symlink":
        original = destination.with_name("original")
        destination.rename(original)
        destination.symlink_to(original)
    if kind == "foreign-template":
        (destination / ".startup-factory.yml").write_text("schema: 1\n")
        (destination / ".copier-answers.yml").write_text("_src_path: another-source\n")
    # WHEN initializing, THEN fail without modifying existing notes.
    result = initialize(workspace)
    assert result.returncode != 0
    assert (destination / "notes").read_text() == "keep"


@pytest.mark.parametrize(
    "registry",
    [
        "# Owner comment\nprojects: [] # portfolio\n\n# Tools comment\ntooling: []\n",
        "# Owner comment\nprojects:\n  - name: original\n    repo: git@github.com:example-owner/original.git\n    deployed: true\n    url: original.example.com\n\n# Tools comment\ntooling: []\n",
        "projects: [{name: original, repo: 'https://github.com/example-owner/original.git'}]\ntooling: []\n",
    ],
)
def test_register_is_idempotent_and_preserves_owner_data(workspace, registry):
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    path = destination / "projects.yaml"
    path.write_text(registry)
    original = yaml.safe_load(registry)
    project = project_in(destination)
    # WHEN registering a real startup repository twice.
    first = cli(destination, "factory", "register", str(project))
    registered = path.read_text()
    second = cli(destination, "factory", "register", str(project))
    # THEN only name/actual remote are added, no live state is invented.
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert path.read_text() == registered
    data = yaml.safe_load(registered)
    assert data["projects"] == [
        *original["projects"],
        {
            "name": "example-app",
            "repo": "https://github.com/example-owner/example-app.git",
        },
    ]
    for comment in ("# Owner comment", "# Tools comment"):
        if comment in registry:
            assert comment in registered


def test_conflicting_registry_blocks_bootstrap_before_resources(workspace):
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    path = destination / "projects.yaml"
    before = "projects: [{name: example-app, repo: 'https://github.com/other-owner/example-app.git'}]\ntooling: []\n"
    path.write_text(before)
    # WHEN bootstrapping the same name for a different account.
    result = cli(
        destination,
        "bootstrap",
        "--yes",
        "--kind",
        "pitch",
        "--project-name",
        "example-app",
        "--base-domain",
        "example.com",
        "--github-username",
        "example-owner",
    )
    # THEN the real CLI stops before any provider step and preserves the registry.
    assert result.returncode != 0
    assert "another repository" in result.stderr
    assert path.read_text() == before
    assert not (destination / "projects").exists()


def test_bootstrap_uses_factory_folder_and_cancellation_writes_nothing(workspace):
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    # WHEN reviewing the real bootstrap plan in a Factory and cancelling.
    result = cli(
        destination,
        "bootstrap",
        "--kind",
        "pitch",
        "--project-name",
        "example-app",
        "--base-domain",
        "example.com",
        "--github-username",
        "example-owner",
        answers="n\n",
    )
    # THEN there is no redundant folder question, and no project is created.
    assert result.returncode == 0, result.stderr
    assert str(destination / "projects/startups/example-app") in result.stdout
    assert "Project folder" not in result.stdout
    assert "Cancelled" in result.stdout
    assert not (destination / "projects").exists()


@pytest.mark.parametrize(
    "invalid",
    [
        "projects: [\n",
        "projects: wrong\n",
        "projects: [{name: duplicate, repo: a}, {name: duplicate, repo: b}]\n",
    ],
)
def test_invalid_registry_stops_with_actionable_error(workspace, invalid):
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    path = destination / "projects.yaml"
    path.write_text(invalid)
    result = cli(destination, "factory", "register", str(project_in(destination)))
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert path.read_text() == invalid


def test_registration_rejects_credential_bearing_remote(workspace):
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    project = project_in(
        destination,
        repo="https://synthetic-token@github.com/example-owner/example-app.git",
    )
    # WHEN a remote contains credentials, THEN none enter the registry or diagnostics.
    result = cli(destination, "factory", "register", str(project))
    assert result.returncode != 0
    assert (
        "synthetic-token"
        not in result.stdout
        + result.stderr
        + (destination / "projects.yaml").read_text()
    )


@pytest.mark.parametrize("fail_secret", [False, True])
def test_real_bootstrap_registers_created_repository_even_after_failure(
    workspace, monkeypatch, fail_secret
):
    import httpx
    from click.testing import CliRunner

    from cli.startup import cli as entrypoint

    assert initialize(workspace).returncode == 0
    source, destination = workspace
    # GIVEN a real pitch template, bare GitHub repository and provider boundaries.
    pitch = source.parent / "pitch"
    pitch.mkdir()
    (pitch / "copier.yml").write_text(
        "project_name: example-app\nbase_domain: app.example.com\ngithub_username: example-owner\n"
    )
    (pitch / ".copier-answers.yml.jinja").write_text(
        "{{ _copier_answers | to_nice_yaml }}\n"
    )
    (pitch / "README.md.jinja").write_text("# {{ project_name }}\n")
    git(pitch, "init", "-b", "main")
    git(pitch, "add", "-A")
    git(
        pitch,
        "-c",
        "user.name=Factory tests",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "pitch",
    )
    remote = source.parent / "github.git"
    git(source.parent, "init", "--bare", str(remote))
    binaries = source.parent / "bin"
    binaries.mkdir()
    gh = binaries / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        + """import os, subprocess, sys
args = sys.argv[1:]
if args[:2] == ['auth', 'status']: print("- Token scopes: 'repo', 'read:packages'"); sys.exit(0)
if args and args[0] == 'api': print('X-OAuth-Scopes: repo, read:packages'); sys.exit(0)
if args[:2] == ['repo', 'view']: sys.exit(0 if 'deploy-your-startup' in args[2] or os.environ.get('CREATED_REPO') else 1)
if args[:2] == ['repo', 'create']:
    subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/example-owner/example-app.git'], check=True)
    sys.exit(0)
if args[:2] == ['secret', 'set']: sys.exit(17 if os.environ.get('FAIL_SECRET') else 0)
sys.exit(99)
"""
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    config = {
        "user.name": "Factory tests",
        "user.email": "test@example.invalid",
        "commit.gpgsign": "false",
        f"url.{remote}.insteadOf": "https://github.com/example-owner/example-app.git",
    }
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(config)))
    for i, (key, value) in enumerate(config.items()):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", value)
    if fail_secret:
        monkeypatch.setenv("FAIL_SECRET", "1")
    calls = []

    def cloudflare_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/user/tokens/verify"):
            result = {}
        elif url.endswith("/accounts"):
            result = [{"id": "local-account"}]
        elif url.endswith("/zones"):
            result = [
                {
                    "id": "zone",
                    "name": "example.com",
                    "name_servers": [],
                    "status": "active",
                }
            ]
        elif "/dns_records" in url:
            result = []
        elif "/domains/" in url:
            result = {"status": "active"}
        elif "/pages/projects/" in url:
            result = {"subdomain": "example-app.pages.dev"}
        else:
            raise AssertionError(f"Unexpected provider GET {url}")
        return httpx.Response(200, json={"success": True, "result": result})

    def cloudflare_post(url, **kwargs):
        assert "/domains" in url or "/dns_records" in url
        calls.append(url)
        return httpx.Response(200, json={"success": True, "result": {}})

    monkeypatch.setattr(httpx, "get", cloudflare_get)
    monkeypatch.setattr(httpx, "post", cloudflare_post)
    monkeypatch.chdir(destination)
    options = [
        "bootstrap",
        "--yes",
        "--kind",
        "pitch",
        "--project-name",
        "example-app",
        "--base-domain",
        "app.example.com",
        "--github-username",
        "example-owner",
        "--template",
        str(pitch),
        "--template-version",
        "HEAD",
        "--cloudflare-token",
        "synthetic-token",
        "--cloudflare-account-id",
        "local-account",
    ]
    # WHEN the actual bootstrap creates the repository and finishes or fails later.
    first = CliRunner().invoke(entrypoint, options)
    assert first.exit_code == (1 if fail_secret else 0), first.output
    # THEN the real registry records only the actual remote, even on interruption.
    registry = (destination / "projects.yaml").read_text()
    assert yaml.safe_load(registry)["projects"] == [
        {
            "name": "example-app",
            "repo": "https://github.com/example-owner/example-app.git",
        }
    ]
    assert "Registered example-app" in first.output
    if fail_secret:
        assert "Command failed: gh secret set" in first.output
        monkeypatch.delenv("FAIL_SECRET")
    # WHEN resuming, THEN project edits survive and the registry does not grow.
    (destination / "projects/startups/example-app/README.md").write_text(
        "Owner customization\n"
    )
    second = CliRunner().invoke(entrypoint, options)
    assert second.exit_code == 0, second.output
    assert (destination / "projects.yaml").read_text() == registry
    assert (
        destination / "projects/startups/example-app/README.md"
    ).read_text() == "Owner customization\n"
    assert calls
