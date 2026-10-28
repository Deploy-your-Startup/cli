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


@pytest.fixture(autouse=True)
def isolated_factory_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "state-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "state-config"))


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
    # Mirror the system tools without kubectl; CI runners preinstall it.
    system = tmp_path / "system"
    system.mkdir()
    for directory in (Path("/usr/bin"), Path("/bin")):
        for tool in directory.iterdir():
            target = system / tool.name
            if tool.name != "kubectl" and not target.exists():
                target.symlink_to(tool)
    path = os.pathsep.join((str(binaries), str(system)))
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
if args[:2] == ['repo', 'view'] and '--json' in args: print('PRIVATE'); sys.exit(0)
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
        assert "Could not set the GitHub secret CLOUDFLARE_API_TOKEN" in first.output
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


def test_onboarding_offers_a_free_folder_when_the_default_is_taken(workspace, tmp_path):
    source, _ = workspace
    # GIVEN an unrelated workspace already occupies the default Factory folder.
    home = tmp_path / "home"
    occupied = home / "Projects/startup-factory"
    occupied.mkdir(parents=True)
    (occupied / "notes").write_text("keep")
    # WHEN onboarding, first choosing the occupied folder, then the suggestion.
    result = cli(
        tmp_path,
        "factory",
        "onboard",
        "--template",
        str(source),
        "--version",
        "v1.0.0",
        answers=f"{occupied}\n\n",
        # System tools only: no Homebrew or workspace tools for setup.
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    # THEN the user is asked again, but missing tools prevent creating the Factory.
    output = result.stdout + result.stderr
    suggestion = home / "Projects/startup-factory-2"
    assert f"[{suggestion}]" in output
    assert "Choose another folder" in output
    assert not suggestion.exists()
    assert (occupied / "notes").read_text() == "keep"
    assert sorted(p.name for p in occupied.iterdir()) == ["notes"]
    # The guided flow continues itself, so it prints no manual next steps.
    assert "Next: cd" not in output
    # Without workspace tools, setup stops with an actionable message.
    assert result.returncode != 0
    assert "existing Factory files are preserved" in result.stderr


@pytest.mark.parametrize("command", ["init", "onboard"])
@pytest.mark.parametrize("existing", [False, True])
def test_missing_tools_stop_before_factory_creation(workspace, command, existing):
    # GIVEN a machine without Homebrew and Factory workspace tools.
    source, destination = workspace
    before = {}
    if existing:
        assert initialize(workspace).returncode == 0
        (destination / "owner-notes").write_text("keep")
        before = {p.name: p.read_bytes() for p in destination.iterdir()}
    # WHEN starting setup through either public Factory entrypoint.
    result = cli(
        source.parent,
        "factory",
        command,
        "--template",
        str(source),
        "--version",
        "v1.0.0",
        "--project-dir",
        str(destination),
        *(["--setup"] if command == "init" else []),
        env={"PATH": str(source.parent / "no-tools")},
    )
    # THEN the user sees all missing tools before any Factory files are written.
    assert result.returncode == 1
    assert "Missing Factory tools: mise, uv, yq, gh" in result.stderr
    assert "brew install mise uv yq gh" in result.stderr
    if existing:
        assert {p.name: p.read_bytes() for p in destination.iterdir()} == before
    else:
        assert not destination.exists()
    assert "Factory created" not in result.stdout


def progress(tmp_path):
    import json

    paths = list(tmp_path.rglob("state.json"))
    assert len(paths) == 1
    return paths[0], json.loads(paths[0].read_text())


def test_factory_failure_before_folder_creation_is_discoverable(workspace, tmp_path):
    # GIVEN missing setup prerequisites, WHEN creation fails before rendering.
    source, destination = workspace
    result = initialize(workspace, "--setup", env={"PATH": "/usr/bin:/bin"})
    assert result.returncode != 0
    assert not destination.exists()
    # THEN a separate process recovers the exact folder, failed step and next command.
    status = cli(tmp_path, "factory", "status")
    assert status.returncode == 0, status.stderr
    assert str(destination) in status.stdout
    assert "Current step: prerequisites" in status.stdout
    assert "Destination folder does not exist" in status.stdout
    assert "startup factory init --resume" in result.stderr
    _, saved = progress(tmp_path)
    assert saved["source"] == str(source)
    assert saved["setup_requested"] is True
    assert saved["workflow_failure"] == "ClickException"


def test_failed_setup_resumes_saved_folder_and_preserves_owner_files(
    workspace, tmp_path
):
    # GIVEN installed tools whose runtime installation fails at the external mise boundary.
    tools = tmp_path / "tools"
    tools.mkdir()
    calls = tmp_path / "calls"
    broken = tmp_path / "fail-install"
    broken.touch()
    for name in ("uv", "yq", "gh"):
        tool = tools / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    mise = tools / "mise"
    mise.write_text(
        f'#!/bin/sh\necho "$*" >> "{calls}"\nif [ "$1" = install ] && [ -f "{broken}" ]; then echo synthetic-private-error >&2; exit 17; fi\n'
    )
    mise.chmod(0o755)
    environment = {"PATH": str(tools) + os.pathsep + os.environ["PATH"]}
    first = initialize(workspace, "--setup", env=environment)
    assert first.returncode != 0
    _, destination = workspace
    (destination / "README.md").write_text("owner changes")
    _, saved = progress(tmp_path)
    assert saved["workflow_step"] == "runtimes"
    assert saved["workflow_last_completed"] == "trust"
    assert "synthetic-private-error" not in str(saved)
    # WHEN another CLI process resumes without the original folder or options.
    broken.unlink()
    second = cli(tmp_path, "factory", "init", "--resume", env=environment)
    # THEN the saved setup completes and owner data survives; prerequisites are rechecked.
    assert second.returncode == 0, second.stdout + second.stderr
    assert (destination / "README.md").read_text() == "owner changes"
    assert "exec -- ./scripts/init.sh" in calls.read_text()
    _, saved = progress(tmp_path)
    assert saved["workflow_step"] == "complete"
    assert saved["workflow_failure"] is None
    assert calls.read_text().count("trust ") == 2


def test_resume_publication_uses_snapshot_without_source(workspace, tmp_path):
    import json

    # GIVEN a saved publication checkpoint after some files reached the destination.
    assert initialize(workspace).returncode == 0
    source, destination = workspace
    path, saved = progress(tmp_path)
    saved.update(step="publish", last_completed="render")
    path.write_text(json.dumps(saved))
    (destination / "README.md").unlink()
    source.rename(source.with_name("source-unavailable"))
    # WHEN resuming in another process, THEN missing files come from the verified snapshot.
    result = cli(tmp_path, "factory", "init", "--resume")
    assert result.returncode == 0, result.stderr
    assert (destination / "README.md").read_text() == f"# {destination.name}\n"
    assert progress(tmp_path)[1]["step"] == "complete"


def test_resume_publication_preserves_conflicting_file(workspace, tmp_path):
    import json

    # GIVEN an interrupted publication and a conflicting owner edit.
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    path, saved = progress(tmp_path)
    saved["step"] = "publish"
    path.write_text(json.dumps(saved))
    (destination / "README.md").write_text("owner edit")
    # WHEN resumed, THEN it stops without overwriting or marking publication complete.
    result = cli(tmp_path, "factory", "init", "--resume")
    assert result.returncode != 0
    assert "Existing file differs; preserved" in result.stderr
    assert (destination / "README.md").read_text() == "owner edit"
    assert progress(tmp_path)[1]["step"] == "publish"


def test_resume_rejects_conflicting_plan_and_status_reads_actual_files(
    workspace, tmp_path
):
    # GIVEN a saved Factory, WHEN resuming with another version, THEN reject the changed plan.
    assert initialize(workspace).returncode == 0
    _, destination = workspace
    result = cli(tmp_path, "factory", "init", "--resume", "--version", "v999")
    assert result.returncode != 0
    assert "differ from the saved" in result.stderr
    # WHEN an owner changes a generated file, THEN status reports disk state.
    (destination / "README.md").write_text("owner changes")
    status = cli(tmp_path, "factory", "status", "--project-dir", str(destination))
    assert status.returncode == 0, status.stderr
    assert "Generated file changed: README.md" in status.stdout


def test_credential_urls_are_rejected_before_saving_or_printing(workspace, tmp_path):
    # GIVEN a URL containing a credential, WHEN starting, THEN it never enters progress or output.
    result = cli(
        tmp_path,
        "factory",
        "init",
        "--template",
        "https://user:synthetic-secret@example.invalid/template",
    )
    assert result.returncode != 0
    assert "credential-free" in result.stderr
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert not list(tmp_path.rglob("state.json"))


def test_resume_without_state_is_actionable(tmp_path):
    # GIVEN no saved state, WHEN resuming, THEN fail without creating a new Factory.
    result = cli(tmp_path, "factory", "init", "--resume")
    assert result.returncode != 0
    assert "No saved Factory setup" in result.stderr
    assert not list(tmp_path.rglob("state.json"))
