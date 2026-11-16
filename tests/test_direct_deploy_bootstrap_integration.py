"""GIVEN templates old and new, WHEN bootstrap settles its shared deployment, THEN only old ones need an owner copy."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENVOPS = """_subdirectory: template
_templates_suffix: ''
_envops:
  variable_start_string: '§§deploy_your_startup.'
  variable_end_string: '§§'
  block_start_string: '§§%'
  block_end_string: '%§§'
  keep_trailing_newline: true
project_name: {type: str}
base_domain: {type: str}
github_username: {type: str}
"""
DIRECT = (
    ENVOPS + "deploy_repository: {type: str, default: Deploy-your-Startup/deploy}\n"
    "deploy_ref: {type: str, default: main}\n"
)
DIRECT_WORKFLOW = (
    "jobs:\n  deploy:\n    uses: §§deploy_your_startup.deploy_repository§§"
    "/.github/workflows/deploy-pages.yml@§§deploy_your_startup.deploy_ref§§\n"
)
ANSWERS = {
    "template/.copier-answers.yml": "§§deploy_your_startup._copier_answers | to_nice_yaml§§"
}
LEGACY_WORKFLOW = (
    "jobs:\n  deploy:\n    uses: §§deploy_your_startup.github_username§§"
    "/deploy-your-startup/.github/workflows/deploy-pages.yml@main\n"
)
SCRIPT = r"""
import json, sys
from pathlib import Path
from cli.wizard.context import BootstrapContext
from cli.wizard.steps.pitch_project import PitchProjectStep
from cli.wizard.steps.shared_deployment import SharedDeploymentStep, choose_shared_deployment
payload = json.load(sys.stdin)
ctx = BootstrapContext('landing', 'demo.example', '', 'example-owner', '18', '',
                       Path(payload['output']), kind='pitch', template_source=payload['template'],
                       template_version='HEAD', deployment_ref=payload['ref'], non_interactive=True)
choose_shared_deployment(ctx)
ready = SharedDeploymentStep().check(ctx)
if ready:
    PitchProjectStep().run(ctx)
print(json.dumps({'direct': ctx.direct_deploy, 'ref': ctx.deployment_ref,
                  'release': ctx.deployment_release, 'ready': ready}))
"""


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def committed(path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(content)
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, "commit", "-qm", "fixture")
    return path


@pytest.fixture
def environment(tmp_path, monkeypatch):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "GIT_AUTHOR_NAME": "Integration",
           "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Integration",
           "GIT_COMMITTER_EMAIL": "test@example.com"}  # fmt: skip
    for name in ("GITHUB_ACTIONS", "CI", "GITHUB_TOKEN"):
        env.pop(name, None)
    # The fixture's own commits use the same synthetic identity as the CLI processes.
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.setenv(name, env[name])
    # GIVEN Deploy-your-Startup/deploy with two releases, served from a local bare repository.
    (tmp_path / "deploy").mkdir()
    work = committed(tmp_path / "deploy", {"roles/sample.yml": "first\n"})
    git(work, "tag", "-a", "-m", "v1.0.0", "v1.0.0")
    (work / "roles/sample.yml").write_text("second\n")
    git(work, "commit", "-qam", "second")
    git(work, "tag", "-a", "-m", "v1.1.0", "v1.1.0")
    remote = tmp_path / "deploy.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(remote))
    env["STARTUP_ANSIBLE_REPO_URL"] = remote.as_uri()
    # AND a GitHub CLI that knows no repositories and records every call.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "gh-calls"
    (binaries / "gh").write_text(f'#!/bin/sh\necho "$*" >> {calls}\nexit 1\n')
    (binaries / "gh").chmod(0o755)
    env["PATH"] = f"{binaries}{os.pathsep}{env['PATH']}"
    commits = {
        tag: git(work, "rev-parse", f"{tag}^{{commit}}") for tag in ("v1.0.0", "v1.1.0")
    }
    return tmp_path, env, commits, calls


def bootstrap(environment, template_files, ref="main"):
    tmp_path, env, _commits, _calls = environment
    template = committed(tmp_path / "template", {**ANSWERS, **template_files})
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], input=json.dumps({
            "output": str(tmp_path / "projects"), "template": str(template), "ref": ref}),
        text=True, capture_output=True, env=env, cwd=tmp_path, check=False, timeout=120,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1]), tmp_path / "projects/landing"


def test_a_direct_template_is_pinned_to_the_latest_release_without_an_owner_copy(
    environment,
):
    _tmp, _env, commits, calls = environment
    # WHEN a template that asks for deploy_repository is bootstrapped.
    state, project = bootstrap(
        environment,
        {
            "copier.yml": DIRECT,
            "template/.github/workflows/deploy.yml": DIRECT_WORKFLOW,
        },
    )
    # THEN it calls Deploy-your-Startup/deploy at the latest release, and no owner copy is looked up.
    assert state == {
        "direct": True,
        "ref": commits["v1.1.0"],
        "release": "v1.1.0",
        "ready": True,
    }
    workflow = (project / ".github/workflows/deploy.yml").read_text()
    assert (
        f"uses: Deploy-your-Startup/deploy/.github/workflows/deploy-pages.yml@{commits['v1.1.0']}\n"
        in workflow
    )
    assert (
        "deploy_repository: Deploy-your-Startup/deploy"
        in (project / ".copier-answers.yml").read_text()
    )
    assert not calls.exists()


def test_a_requested_deployment_ref_is_kept_for_a_direct_template(environment):
    _tmp, _env, commits, _calls = environment
    # WHEN the owner asks for an earlier release commit.
    state, project = bootstrap(
        environment, {"copier.yml": DIRECT, "template/.github/workflows/deploy.yml": DIRECT_WORKFLOW},
        ref=commits["v1.0.0"],
    )  # fmt: skip
    # THEN exactly that commit is rendered.
    assert state["ref"] == commits["v1.0.0"] and state["release"] is None
    assert (
        f"@{commits['v1.0.0']}\n"
        in (project / ".github/workflows/deploy.yml").read_text()
    )


def test_an_older_template_still_needs_the_owner_copy(environment):
    _tmp, _env, _commits, calls = environment
    # WHEN a template without deploy_repository is bootstrapped.
    state, project = bootstrap(
        environment,
        {
            "copier.yml": ENVOPS,
            "template/.github/workflows/deploy.yml": LEGACY_WORKFLOW,
        },
    )
    # THEN the owner copy is checked on GitHub and, missing, left for the sync step to create.
    assert state == {"direct": False, "ref": "main", "release": None, "ready": False}
    assert "repo view example-owner/deploy-your-startup" in calls.read_text()
    assert not project.exists()


@pytest.mark.parametrize(
    ("template", "version"),
    [
        ("DEFAULT_TEMPLATE", "DEFAULT_TEMPLATE_VERSION"),
        ("VUE_TEMPLATE", "VUE_TEMPLATE_VERSION"),
        ("VUE_NESTJS_TEMPLATE", "VUE_NESTJS_TEMPLATE_VERSION"),
        ("PITCH_TEMPLATE", "PITCH_TEMPLATE_VERSION"),
    ],
)
def test_every_pinned_template_bootstraps_without_an_owner_copy(template, version):
    # GIVEN a template release this CLI offers by default, as published on GitHub.
    from cli import template_commands

    # WHEN its Copier questions are read, THEN it asks for deploy_repository.
    assert template_commands.template_uses_deploy(
        getattr(template_commands, template), getattr(template_commands, version)
    )


@pytest.mark.parametrize(
    ("template", "version"),
    [
        ("DEFAULT_TEMPLATE", "DEFAULT_TEMPLATE_VERSION"),
        ("VUE_TEMPLATE", "VUE_TEMPLATE_VERSION"),
        ("VUE_NESTJS_TEMPLATE", "VUE_NESTJS_TEMPLATE_VERSION"),
    ],
)
def test_every_pinned_server_template_grants_the_shared_build_its_permissions(
    template, version
):
    # GIVEN a server template release this CLI offers by default.
    from copier.main import Worker

    from cli import template_commands

    with Worker(
        src_path=getattr(template_commands, template),
        vcs_ref=getattr(template_commands, version),
        skip_tasks=True,
        quiet=True,
    ) as worker:
        workflows = worker.template.local_abspath / "template/.github/workflows"
        # WHEN its shared build caller is read.
        caller = next(workflows.glob("build-and-deploy-backend.yml*")).read_text()
    # THEN it grants what the other owner's nested jobs request; without it the run never starts.
    assert "/.github/workflows/build-and-deploy-service.yml@" in caller
    assert "      id-token: write\n" in caller
    assert "      packages: write\n" in caller
