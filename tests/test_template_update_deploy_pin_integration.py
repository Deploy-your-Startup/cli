"""GIVEN a project moved to Deploy-your-Startup/deploy, WHEN its template is updated, THEN its pin holds."""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from copier import run_copy

from cli.startup import cli
from tests.test_template_commands import commit, init

PIN = "6" * 40
OLD = "0" * 40
LEGACY = """project_name: {type: str}
github_username: {type: str}
deploy_repo_name: {type: str, default: deploy-your-startup}
deploy_ref: {type: str, default: main}
"""
DIRECT = """project_name: {type: str}
github_username: {type: str}
deploy_repository: {type: str, default: Deploy-your-Startup/deploy}
deploy_ref: {type: str, default: main}
"""
LEGACY_WORKFLOW = (
    "jobs:\n  deploy:\n    uses: {{ github_username }}/{{ deploy_repo_name }}"
    "/.github/workflows/deploy.yml@{{ deploy_ref }}\n"
)
DIRECT_WORKFLOW = (
    "jobs:\n  deploy:\n    uses: {{ deploy_repository }}"
    "/.github/workflows/deploy.yml@{{ deploy_ref }}\n"
)


def template_release(template: Path, copier: str, workflow: str, readme: str, tag: str):
    (template / "copier.yml").write_text(copier)
    (template / ".copier-answers.yml.jinja").write_text(
        "{{ _copier_answers | to_nice_yaml }}"
    )
    (template / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (template / ".github/workflows/deploy.yml.jinja").write_text(workflow)
    (template / "README.md").write_text(readme)
    commit(template, tag)
    from cli.template_commands import git

    git(template, "tag", tag)


def migrated_project(tmp_path, new_workflow):
    # GIVEN a project rendered from an owner-copy template and then moved with
    # `startup migrate deploy`: its workflow and role pin name a deploy release.
    template = tmp_path.resolve() / "template"
    init(template)
    template_release(template, LEGACY, LEGACY_WORKFLOW, "v1\n", "v1.0.0")
    project = tmp_path.resolve() / "project"
    run_copy(str(template), str(project), vcs_ref="v1.0.0", defaults=True, quiet=True,
             data={"project_name": "demo", "github_username": "example-owner", "deploy_ref": OLD})  # fmt: skip
    init_project = project / ".git"
    assert not init_project.exists()
    from cli.template_commands import git

    git(project, "init", "-b", "main")
    for key, value in (
        ("user.name", "T"),
        ("user.email", "t@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(project, "config", key, value)
    (project / "deployment").mkdir()
    workflow = project / ".github/workflows/deploy.yml"
    workflow.write_text(
        "jobs:\n  deploy:\n    uses: Deploy-your-Startup/deploy"
        f"/.github/workflows/deploy.yml@{PIN} # v1.0.0\n"
    )
    (project / "deployment/shared-roles.ref").write_text(PIN + "\n")
    commit(project, "migrated")
    # AND a new template release that asks for deploy_repository.
    template_release(template, DIRECT, new_workflow, "v2\n", "v2.0.0")
    return project, workflow


@pytest.mark.parametrize("ansible", [True, False])
def test_template_update_keeps_the_projects_shared_deployment_pin(tmp_path, ansible):
    # GIVEN a project moved to a deploy release and a template release with deploy_repository.
    project, workflow = migrated_project(tmp_path, DIRECT_WORKFLOW)
    if not ansible:
        (project / "deployment/shared-roles.ref").unlink()
        (project / "deployment").rmdir()
        commit(project, "workflow-only project")
    # WHEN the application template is updated.
    result = CliRunner().invoke(
        cli,
        ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"],
    )
    # THEN template changes arrive, but the shared deployment stays on the project's pin.
    assert result.exit_code == 0, result.output
    assert (project / "README.md").read_text() == "v2\n"
    text = workflow.read_text()
    assert (
        f"uses: Deploy-your-Startup/deploy/.github/workflows/deploy.yml@{PIN}" in text
    )
    assert (
        OLD not in text and "deploy-your-startup" not in text and "<<<<<<<" not in text
    )
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert answers["deploy_ref"] == PIN
    assert answers["deploy_repository"] == "Deploy-your-Startup/deploy"
    assert "Kept this project's shared deployment pin" in result.output


def test_a_real_change_to_the_shared_reference_stays_a_conflict(tmp_path):
    # GIVEN a template release that calls a different shared workflow.
    renamed = DIRECT_WORKFLOW.replace("workflows/deploy.yml", "workflows/deploy-v2.yml")
    project, workflow = migrated_project(tmp_path, renamed)
    # WHEN the application template is updated.
    result = CliRunner().invoke(
        cli,
        ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"],
    )
    # THEN the owner resolves it: only comment-level differences are kept automatically.
    assert result.exit_code != 0
    assert "conflicts" in result.output
    assert "<<<<<<<" in workflow.read_text()


@pytest.mark.parametrize("ansible", [True, False])
def test_template_update_preserves_the_one_active_reference(tmp_path, ansible):
    # GIVEN a real template with the single-version entry and historical Copier answers.
    template = tmp_path.resolve() / "template"
    init(template)
    workflows = template / ".github/workflows"
    workflows.mkdir(parents=True)
    entry_source = workflows / "startup.yml.jinja"
    entry_source.write_text(
        "jobs:\n  runtime:\n    uses: {{ deploy_repository }}"
        "/.github/workflows/project.yml@{{ deploy_ref }}\n"
    )
    caller = "jobs:\n  deploy:\n    uses: ./.github/workflows/startup.yml\n    with:\n      operation: deploy\n"
    template_release(template, DIRECT, caller, "v1\n", "v1.0.0")
    project = tmp_path.resolve() / "project"
    run_copy(str(template), str(project), vcs_ref="v1.0.0", defaults=True, quiet=True,
             data={"project_name": "demo", "github_username": "example", "deploy_ref": OLD})  # fmt: skip
    from cli.template_commands import git

    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "Template integration")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "config", "commit.gpgsign", "false")
    entry = project / ".github/workflows/startup.yml"
    entry.write_text(entry.read_text().replace(OLD, PIN + " # v1.2.0"))
    if ansible:
        (project / "deployment").mkdir()
        (project / "deployment/playbook.yml").write_text("[]\n")
    commit(project, "configured release independent of original answers")
    template_release(template, DIRECT, caller, "v2\n", "v2.0.0")
    # WHEN previewing and applying the application template update.
    before = entry.read_text()
    arguments = [
        "template",
        "update",
        "--project-dir",
        str(project),
        "--version",
        "v2.0.0",
    ]
    preview = CliRunner().invoke(cli, [*arguments, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert entry.read_text() == before
    result = CliRunner().invoke(cli, arguments)
    # THEN the active version remains pinned and no duplicate pin files appear.
    assert result.exit_code == 0, result.output
    assert entry.read_text() == before
    assert (project / "README.md").read_text() == "v2\n"
    assert not list(project.glob("**/shared-roles.ref"))
    assert not list(project.glob("**/shared-roles.sha256"))
