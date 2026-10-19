from pathlib import Path

import pytest
import yaml
from click import BadParameter
from click.testing import CliRunner

from cli.startup import cli
from cli.template_commands import (
    adopt_project,
    git,
    parse_data,
    render_project,
    template_authentication,
)
from cli.wizard.context import BootstrapContext
from cli.wizard.steps import pitch_project as pitch_project_step
from cli.wizard.steps import project as project_step


def commit(repo: Path, message: str):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def init(repo: Path):
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Template tests")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "tag.gpgsign", "false")
    git(repo, "config", "core.hooksPath", "/dev/null")


@pytest.fixture
def repos(tmp_path):
    template = tmp_path.resolve() / "template"
    init(template)
    (template / "copier.yml").write_text("project_name: demo\n")
    (template / ".copier-answers.yml.jinja").write_text(
        "{{ _copier_answers | to_nice_yaml }}"
    )
    (template / "make.sh").write_text("setup=v1\n\ncustom=template\n")
    (template / "unused.py").write_text("# Intentionally absent in the project\n")
    vault = template / "deployment/group_vars"
    vault.mkdir(parents=True)
    (vault / "production.yml").write_text(
        "base_domain: example.com\nsecret: !vault |\n  $ANSIBLE_VAULT;dummy-template\n"
    )
    commit(template, "baseline")
    git(template, "tag", "v1.0.0")

    project = tmp_path.resolve() / "project"
    init(project)
    git(project, "remote", "add", "origin", "git@github.com:owner/project.git")
    (project / "make.sh").write_text("setup=v1\n\ncustom=project\n")
    (project / "app.py").write_text("# Project business logic\n")
    vault = project / "deployment/group_vars"
    vault.mkdir(parents=True)
    (vault / "production.yml").write_text(
        "base_domain: example.com\nsecret: !vault |\n  $ANSIBLE_VAULT;dummy-project\n"
    )
    commit(project, "existing project")
    return template, project


def test_authentication_metadata_honors_selected_template_version(repos):
    template, _ = repos
    (template / "startup-template.yml").write_text("authentication: auth0\n")
    commit(template, "declare auth0")
    assert template_authentication(str(template), "HEAD") == "auth0"
    assert template_authentication(str(template), "v1.0.0") is None


def test_adopt_preserves_existing_and_deleted_files(repos):
    template, project = repos
    before = {
        p: p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    adopt_project(project, source=str(template), version="v1.0.0", data={})
    assert git(project, "status", "--porcelain") == "?? .copier-answers.yml"
    assert not (project / "unused.py").exists()
    assert all(p.read_bytes() == content for p, content in before.items())
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert answers["_commit"] == "v1.0.0"
    assert "dummy-project" not in (project / ".copier-answers.yml").read_text()


def test_update_preserves_customizations_vault_and_dry_run(repos):
    template, project = repos
    adopt_project(project, source=str(template), version="v1.0.0", data={})
    commit(project, "adopt")
    vault = (project / "deployment/group_vars/production.yml").read_bytes()
    (template / "make.sh").write_text("setup=v2\n\ncustom=template\n")
    (template / "new.py").write_text("# Newly added template file\n")
    (template / "deployment/group_vars/production.yml").write_text(
        "secret: overwritten\n"
    )
    commit(template, "update")
    git(template, "tag", "v2.0.0")
    runner = CliRunner()
    args = ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"]
    preview = runner.invoke(cli, [*args, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "setup=v2" in preview.output
    assert "new.py" in preview.output
    assert not git(project, "status", "--porcelain")
    assert not (project / "new.py").exists()
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert (project / "make.sh").read_text() == "setup=v2\n\ncustom=project\n"
    assert (project / "deployment/group_vars/production.yml").read_bytes() == vault
    assert not (project / "unused.py").exists()
    assert (project / "new.py").exists()
    commit(project, "update")
    repeat = runner.invoke(cli, args)
    assert repeat.exit_code == 0, repeat.output
    assert not git(project, "status", "--porcelain")


def test_update_reports_conflict_and_preview_preserves_original(repos):
    template, project = repos
    adopt_project(project, source=str(template), version="v1.0.0", data={})
    (project / "make.sh").write_text("setup=project\n\ncustom=project\n")
    commit(project, "adopt and customize")
    (template / "make.sh").write_text("setup=template-v2\n\ncustom=template\n")
    commit(template, "conflicting update")
    git(template, "tag", "v2.0.0")
    args = ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"]
    preview = CliRunner().invoke(cli, [*args, "--dry-run"])
    assert preview.exit_code != 0
    assert "conflicts" in preview.output
    assert not git(project, "status", "--porcelain")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "conflicts" in result.output
    assert "<<<<<<<" in (project / "make.sh").read_text()


def test_update_reports_harness_files_that_drifted_from_the_template(repos):
    # GIVEN a template that ships ADRs and a testing policy, and a project adopted
    # with an edited ADR 0001, no ADR 0006, a project ADR in the template range
    # and a correctly numbered project ADR.
    template, project = repos
    adrs = template / "docs/architecture"
    adrs.mkdir(parents=True)
    (adrs / "0001-test-strategy.md").write_text("# Integration and E2E tests\n")
    (adrs / "0006-business-logic-in-services.md").write_text("# Services\n")
    (template / "docs/testing-policy.md").write_text("# Testing policy\n")
    commit(template, "ship harness")
    git(template, "tag", "v1.1.0")
    project_adrs = project / "docs/architecture"
    project_adrs.mkdir(parents=True)
    (project_adrs / "0001-test-strategy.md").write_text("# Outdated test strategy\n")
    (project_adrs / "0007-card-recognition.md").write_text("# Card recognition\n")
    (project_adrs / "0100-retailer-images.md").write_text("# Retailer images\n")
    (project / "docs/testing-policy.md").write_text("# Testing policy\n")
    commit(project, "project harness")
    adopt_project(project, source=str(template), version="v1.1.0", data={})
    commit(project, "adopt with drifted harness")
    (template / "make.sh").write_text("setup=v2\n\ncustom=template\n")
    commit(template, "unrelated update")
    git(template, "tag", "v2.0.0")
    args = ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"]

    # WHEN previewing and then applying the update.
    preview = CliRunner().invoke(cli, [*args, "--dry-run"])
    result = CliRunner().invoke(cli, args)

    # THEN both name every drifted harness file, Copier's merge left them as they
    # were, and the correctly numbered project ADR is not reported.
    for output in (preview.output, result.output):
        assert "agent harness files differ from the template at v2.0.0" in output
        assert "differs: docs/architecture/0001-test-strategy.md" in output
        assert "missing: docs/architecture/0006-business-logic-in-services.md" in output
        assert "not in template: docs/architecture/0007-card-recognition.md" in output
        assert "0100-retailer-images.md" not in output
        assert "testing-policy.md" not in output
    assert preview.exit_code == 0, preview.output
    assert result.exit_code == 0, result.output
    assert not (
        project / "docs/architecture/0006-business-logic-in-services.md"
    ).exists()


def test_update_without_harness_drift_prints_no_warning(repos):
    # GIVEN a template that declares its own harness files and a project that
    # carries them unchanged.
    template, project = repos
    (template / "startup-template.yml").write_text("harness:\n  - AGENTS.md\n")
    (template / "AGENTS.md").write_text("# Instructions\n")
    commit(template, "declare harness")
    git(template, "tag", "v1.1.0")
    (project / "startup-template.yml").write_text("harness:\n  - AGENTS.md\n")
    (project / "AGENTS.md").write_text("# Instructions\n")
    commit(project, "project harness")
    adopt_project(project, source=str(template), version="v1.1.0", data={})
    commit(project, "adopt matching harness")
    (template / "make.sh").write_text("setup=v2\n\ncustom=template\n")
    commit(template, "unrelated update")
    git(template, "tag", "v2.0.0")

    # WHEN updating the template.
    result = CliRunner().invoke(
        cli,
        ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"],
    )

    # THEN the update succeeds without a drift warning.
    assert result.exit_code == 0, result.output
    assert "harness files differ" not in result.output


def test_dirty_project_and_duplicate_adoption_are_rejected(repos):
    template, project = repos
    (project / "app.py").write_text("# Uncommitted work\n")
    args = [
        "template",
        "adopt",
        "--project-dir",
        str(project),
        "--template",
        str(template),
        "--version",
        "v1.0.0",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "local changes" in result.output
    commit(project, "customize")
    assert CliRunner().invoke(cli, args).exit_code == 0
    commit(project, "adopt")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "already has" in result.output


def test_secret_fields_are_rejected():
    with pytest.raises(BadParameter, match="public template fields"):
        parse_data(("hcloud_token=secret",))


def test_render_project_uses_existing_placeholder_map(repos, tmp_path):
    template, _ = repos
    target = tmp_path / "generated"
    render_project(
        target,
        {"§§deploy_your_startup.project_name§§": "new-project"},
        source=str(template),
        version="v1.0.0",
    )
    assert (target / "make.sh").exists()
    answers = yaml.safe_load((target / ".copier-answers.yml").read_text())
    assert answers["project_name"] == "new-project"


def test_bootstrap_renders_selected_template_and_resume_preserves_edits(
    repos, tmp_path, monkeypatch
):
    source, _ = repos
    ctx = BootstrapContext(
        project_name="bootstrapped",
        base_domain="example.com",
        additional_domains="",
        github_username="owner",
        postgres_version="17",
        sentry_dsn="",
        output_dir=tmp_path.resolve(),
        provider="byos",
        template_source=str(source),
        template_version="v1.0.0",
    )
    monkeypatch.setattr(
        project_step, "prompt_user_public_key", lambda **_kwargs: "ssh-ed25519 operator"
    )
    monkeypatch.setattr(
        project_step,
        "_generate_ssh_keypair",
        lambda *_args: ("fixture-private", "ssh-ed25519 ci"),
    )
    monkeypatch.setattr(
        project_step, "_generate_docker_config_b64", lambda *_args: "fixture"
    )

    class ReachedVault(Exception):
        pass

    def stop_before_vault(**_kwargs):
        raise ReachedVault

    monkeypatch.setattr("cli.update_vault_secrets.update_secrets", stop_before_vault)
    with pytest.raises(ReachedVault):
        project_step.ProjectStep().run(ctx)
    answers = yaml.safe_load((ctx.project_dir / ".copier-answers.yml").read_text())
    assert answers["project_name"] == "bootstrapped"
    assert answers["_commit"] == "v1.0.0"
    (ctx.project_dir / "make.sh").write_text(
        "# Local edits after interrupted bootstrap\n"
    )
    with pytest.raises(ReachedVault):
        project_step.ProjectStep().run(ctx)
    assert (
        ctx.project_dir / "make.sh"
    ).read_text() == "# Local edits after interrupted bootstrap\n"


def test_pitch_bootstrap_renders_copier_template_and_resume_keeps_edits(tmp_path):
    # GIVEN a pitch template laid out like Deploy-your-Startup/pitch-template
    source = tmp_path.resolve() / "pitch-template"
    init(source)
    (source / "copier.yml").write_text(
        "_subdirectory: template\n"
        "_templates_suffix: ''\n"
        "_envops:\n"
        "  variable_start_string: '§§deploy_your_startup.'\n"
        "  variable_end_string: '§§'\n"
        "  block_start_string: '§§%'\n"
        "  block_end_string: '%§§'\n"
        "  keep_trailing_newline: true\n"
        "project_name: {type: str}\n"
        "base_domain: {type: str}\n"
        "github_username: {type: str}\n"
    )
    (source / "TEMPLATE.md").write_text("Maintainer notes\n")
    page = source / "template/frontend/src/pages"
    page.mkdir(parents=True)
    (page / "index.astro").write_text(
        '---\nconst projectName = "§§deploy_your_startup.project_name§§";\n---\n'
        "<title>{projectName}</title>\n"
    )
    workflow = source / "template/.github/workflows"
    workflow.mkdir(parents=True)
    (workflow / "deploy.yml").write_text(
        "uses: §§deploy_your_startup.github_username§§/deploy-your-startup/"
        ".github/workflows/deploy-pages.yml@main\n"
        "token: ${{ secrets.CLOUDFLARE_API_TOKEN }}\n"
    )
    (source / "template/.copier-answers.yml").write_text(
        "§§deploy_your_startup._copier_answers | to_nice_yaml§§\n"
    )
    commit(source, "pitch baseline")
    git(source, "tag", "v0.1.0")
    ctx = BootstrapContext(
        project_name="launch-page",
        base_domain="launch.example.com",
        additional_domains="",
        github_username="owner",
        postgres_version="17",
        sentry_dsn="",
        output_dir=tmp_path.resolve(),
        kind="pitch",
        template_source=str(source),
        template_version="v0.1.0",
    )

    # WHEN the pitch bootstrap creates the project
    step = pitch_project_step.PitchProjectStep()
    assert not step.check(ctx)
    step.run(ctx)

    # THEN only the generated project is rendered into a fresh repository
    project = ctx.project_dir
    assert git(project, "symbolic-ref", "--short", "HEAD") == "main"
    assert not (project / "TEMPLATE.md").exists()
    assert not (project / "copier.yml").exists()
    assert (
        'const projectName = "launch-page"'
        in (project / "frontend/src/pages/index.astro").read_text()
    )
    deploy = (project / ".github/workflows/deploy.yml").read_text()
    assert "uses: owner/deploy-your-startup/" in deploy
    assert "${{ secrets.CLOUDFLARE_API_TOKEN }}" in deploy
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert answers["_commit"] == "v0.1.0"
    assert answers["base_domain"] == "launch.example.com"

    # AND a resumed bootstrap skips the step instead of overwriting edits
    assert step.check(ctx)
